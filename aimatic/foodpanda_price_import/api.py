import csv
import io
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import frappe
import openpyxl
from frappe import _
from frappe.utils.file_manager import save_file

from aimatic.barcode_utils import barcode_variants
from aimatic.price_export.api import _apply_foodpanda_price_updates

# Same role gate shelf_pricing.api uses for any write to a selling price -
# applying an uploaded Foodpanda catalog file is just another price-write
# surface, not a separate permission model.
_ALLOWED_IMPORT_ROLES = {"Buying Price Control", "System Manager"}

_REQUIRED_COLUMNS = ("price", "sku", "name", "active")
_MAX_FILE_BYTES = 25 * 1024 * 1024
_TRUE_VALUES = {"1", "active", "true", "yes", "y"}


def _require_import_permission():
	if not _ALLOWED_IMPORT_ROLES.intersection(frappe.get_roles()):
		frappe.throw(
			_("You need the Buying Price Control role to import a Foodpanda price list."),
			frappe.PermissionError,
		)


def _normalize_header(value):
	return " ".join(str(value or "").strip().lower().split())


def _cell_text(value):
	if value in (None, ""):
		return ""
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	return str(value).strip()


def _is_active(value):
	if isinstance(value, bool):
		return value
	return _normalize_header(_cell_text(value)) in _TRUE_VALUES


def _load_barcode_map():
	"""Return every Item candidate for every supported GTIN variant.

	A barcode variant can legitimately point at more than one ERP Item. Never
	silently select the first Item: the caller deliberately expands a source
	row to every matched Item, as required for the S7 Foodpanda catalog.
	"""
	barcode_map = defaultdict(set)
	for row in frappe.get_all("Item Barcode", fields=["barcode", "parent"]):
		for variant in barcode_variants(row.barcode):
			barcode_map[variant].add(row.parent)
	return barcode_map


def _resolve_item_codes(barcode_map, raw_values):
	item_codes = set()
	for raw_value in raw_values:
		for variant in barcode_variants(_cell_text(raw_value)):
			item_codes.update(barcode_map.get(variant, ()))
	return item_codes


def _parse_positive_price(value):
	if value in (None, ""):
		return None
	try:
		price = Decimal(str(value).replace(",", "").strip())
	except (InvalidOperation, ValueError):
		return None
	if not price.is_finite() or price <= 0:
		return None
	return round(float(price), 6)


def _build_import_plan(sheet, barcode_map):
	"""Build one final Foodpanda price per ERP Item without writing.

	Every ``barcode *`` column participates in matching. A source row that
	matches multiple ERP Items applies to all of them. If an Item receives
	different prices from multiple source rows, only active source rows are
	considered and the highest active price wins. A conflict with no active
	row is skipped and reported. Non-conflicting rows are imported regardless
	of their Foodpanda active status.
	"""
	header = [_normalize_header(cell.value) for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
	missing = [column for column in _REQUIRED_COLUMNS if column not in header]
	if missing:
		raise ValueError("File is missing required column(s): " + ", ".join(missing))

	barcode_columns = [index for index, value in enumerate(header) if value.startswith("barcode")]
	if not barcode_columns:
		raise ValueError("File must contain at least one barcode column.")

	price_column = header.index("price")
	sku_column = header.index("sku")
	name_column = header.index("name")
	active_column = header.index("active")
	assignments = defaultdict(list)
	exceptions = []
	stats = {
		"source_rows": 0,
		"rows_with_barcode": 0,
		"matched_source_rows": 0,
		"multi_item_rows": 0,
		"unmatched": 0,
		"skipped_bad_price": 0,
	}

	for row_number, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
		if not any(value not in (None, "") for value in values):
			continue
		stats["source_rows"] += 1
		sku = _cell_text(values[sku_column])
		product_name = _cell_text(values[name_column])
		barcodes = [_cell_text(values[index]) for index in barcode_columns]
		barcodes = [barcode for barcode in barcodes if barcode]
		if barcodes:
			stats["rows_with_barcode"] += 1

		price = _parse_positive_price(values[price_column])
		if price is None:
			stats["skipped_bad_price"] += 1
			exceptions.append(
				{
					"reason": "Missing or non-positive price",
					"source_rows": str(row_number),
					"item_code": "",
					"sku": sku,
					"name": product_name,
					"active": _cell_text(values[active_column]),
					"prices": _cell_text(values[price_column]),
					"barcodes": " | ".join(barcodes),
				}
			)
			continue

		item_codes = _resolve_item_codes(barcode_map, barcodes)
		if not item_codes:
			stats["unmatched"] += 1
			exceptions.append(
				{
					"reason": "No ERP Item barcode match",
					"source_rows": str(row_number),
					"item_code": "",
					"sku": sku,
					"name": product_name,
					"active": _cell_text(values[active_column]),
					"prices": str(price),
					"barcodes": " | ".join(barcodes),
				}
			)
			continue

		stats["matched_source_rows"] += 1
		if len(item_codes) > 1:
			stats["multi_item_rows"] += 1
		assignment = {
			"row_number": row_number,
			"sku": sku,
			"name": product_name,
			"active": _is_active(values[active_column]),
			"active_raw": _cell_text(values[active_column]),
			"price": price,
			"barcodes": barcodes,
		}
		for item_code in item_codes:
			assignments[item_code].append(assignment)

	updates = {}
	conflicting_items = resolved_conflicts = skipped_inactive_conflicts = 0
	for item_code, item_assignments in assignments.items():
		prices = {assignment["price"] for assignment in item_assignments}
		if len(prices) == 1:
			updates[item_code] = {"price": next(iter(prices))}
			continue

		conflicting_items += 1
		active_assignments = [assignment for assignment in item_assignments if assignment["active"]]
		if active_assignments:
			updates[item_code] = {"price": max(assignment["price"] for assignment in active_assignments)}
			resolved_conflicts += 1
			continue

		skipped_inactive_conflicts += 1
		exceptions.append(
			{
				"reason": "Conflicting prices; all source rows inactive",
				"source_rows": " | ".join(str(row["row_number"]) for row in item_assignments),
				"item_code": item_code,
				"sku": " | ".join(row["sku"] for row in item_assignments),
				"name": " | ".join(row["name"] for row in item_assignments),
				"active": " | ".join(row["active_raw"] for row in item_assignments),
				"prices": " | ".join(str(row["price"]) for row in item_assignments),
				"barcodes": " | ".join(
					barcode for row in item_assignments for barcode in row["barcodes"]
				),
			}
		)

	stats.update(
		{
			"matched_items": len(assignments),
			"accepted_items": len(updates),
			"conflicting_items": conflicting_items,
			"resolved_conflicts": resolved_conflicts,
			"skipped_inactive_conflicts": skipped_inactive_conflicts,
		}
	)
	return updates, stats, exceptions


def _load_plan(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	file_name = (file_doc.file_name or file_url).lower()
	if not file_name.endswith(".xlsx"):
		raise ValueError("Please upload a Foodpanda Products .xlsx file.")
	content = file_doc.get_content()
	if len(content) > _MAX_FILE_BYTES:
		raise ValueError("The Excel file is too large. Maximum allowed size is 25 MB.")

	workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
	try:
		return _build_import_plan(workbook.worksheets[0], _load_barcode_map())
	finally:
		workbook.close()


def _validate_request(branch, file_url):
	_require_import_permission()
	if not branch:
		frappe.throw(_("Branch is required."))
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Branch {0} does not exist.").format(branch))
	if not file_url:
		frappe.throw(_("Please attach a Foodpanda product export file."))


def _exception_report(log_name, rows):
	if not rows:
		return None
	buffer = io.StringIO()
	fieldnames = [
		"reason",
		"source_rows",
		"item_code",
		"sku",
		"name",
		"active",
		"prices",
		"barcodes",
	]
	writer = csv.DictWriter(buffer, fieldnames=fieldnames)
	writer.writeheader()
	writer.writerows(rows)
	saved = save_file(
		f"{log_name}-exceptions.csv",
		buffer.getvalue().encode("utf-8"),
		"Foodpanda Price Import Log",
		log_name,
		is_private=1,
	)
	return saved.file_url


@frappe.whitelist()
def preview_price_list(branch, file_url):
	"""Read-only preview of a Foodpanda Products workbook."""
	_validate_request(branch, file_url)
	try:
		updates, stats, _exceptions = _load_plan(file_url)
	except ValueError as error:
		frappe.throw(_(str(error)))

	price_list = frappe.db.get_value("Branch", branch, "default_foodpanda_price_list")
	valid_items = {
		row.name
		for row in frappe.get_all(
			"Item",
			filters={"name": ("in", list(updates))},
			fields=["name", "disabled", "is_sales_item"],
			ignore_permissions=True,
		)
		if not row.disabled and row.is_sales_item
	}
	return {
		"branch": branch,
		"price_list": price_list,
		**stats,
		"eligible_items": len(valid_items),
		"invalid_items": len(updates) - len(valid_items),
	}


@frappe.whitelist()
def import_price_list(branch, file_url):
	"""Import a Foodpanda Products workbook into one branch Foodpanda list.

	The branch's regular Selling Price List is neither an eligibility filter
	nor a write target. Foodpanda ``active`` is ignored for ordinary rows; it
	is used only to resolve duplicate source rows carrying different prices.
	"""
	_validate_request(branch, file_url)
	try:
		updates, stats, exceptions = _load_plan(file_url)
	except ValueError as error:
		frappe.throw(_(str(error)))
	if not updates:
		frappe.throw(_("No importable Foodpanda prices were found in the Excel file."))

	result = _apply_foodpanda_price_updates(
		branch,
		updates,
		source_file=file_url,
		skip_invalid=True,
	)
	log_name = result.get("log")
	if log_name:
		frappe.db.set_value("Foodpanda Price Import Log", log_name, "unmatched_count", stats["unmatched"])
		report_url = _exception_report(log_name, exceptions)
		if report_url:
			frappe.db.set_value("Foodpanda Price Import Log", log_name, "unmatched_report", report_url)
			result["unmatched_report"] = report_url

	result.update(stats)
	return result
