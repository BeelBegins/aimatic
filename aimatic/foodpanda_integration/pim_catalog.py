"""Initial, idempotent PIM catalog setup for a Foodpanda outlet."""

import csv
import os
from collections import defaultdict

import frappe
from frappe import _

from aimatic.shelf_pricing.utils import get_or_create_branch_foodpanda_price_list

_COMMIT_BATCH_SIZE = 100


def _barcode(value):
	value = str(value or "").strip()
	if value.endswith(".0") and value[:-2].isdigit():
		value = value[:-2]
	return value if value.isdigit() else ""


def _barcode_variants(value):
	"""Accept the Foodpanda catalog's one additional leading zero."""
	value = _barcode(value)
	if not value:
		return set()
	return {value, value[1:]} if value.startswith("0") else {value}


def _pim_catalog_path(file_url):
	file_name = os.path.basename(str(file_url or ""))
	if not file_name or file_url != f"/files/{file_name}" or not file_name.lower().endswith(".csv"):
		frappe.throw(_("Select a CSV uploaded to the site's public files."))
	if not frappe.db.exists("File", {"file_url": file_url}):
		frappe.throw(_("The selected PIM catalog file does not exist."))
	return frappe.get_site_path("public", "files", file_name)


def _pim_titles_by_barcode(file_url):
	path = _pim_catalog_path(file_url)
	with open(path, encoding="utf-8-sig", newline="") as source:
		rows = csv.DictReader(source)
		required = {"pim_product_name_english", "barcode", "barcode_length"}
		if not required.issubset(rows.fieldnames or set()):
			frappe.throw(_("PIM catalog is missing one of: {0}").format(", ".join(sorted(required))))

		result = defaultdict(set)
		for row in rows:
			barcode = _barcode(row.get("barcode"))
			try:
				barcode_length = int(str(row.get("barcode_length") or "").strip())
			except (TypeError, ValueError):
				continue
			title = (row.get("pim_product_name_english") or "").strip()
			if not barcode or not title or not 8 <= barcode_length <= 14:
				continue
			identity = (str(row.get("master_code") or "").strip(), title)
			for variant in _barcode_variants(barcode):
				result[variant].add(identity)
	return result


def _copy_missing_prices(source_price_list, target_price_list):
	source_rows = frappe.get_all(
		"Item Price",
		filters={"price_list": source_price_list, "selling": 1},
		fields=["item_code", "price_list_rate", "currency", "uom", "valid_from", "valid_upto"],
	)
	by_item = defaultdict(list)
	for row in source_rows:
		by_item[row.item_code].append(row)

	existing_items = set(
		frappe.get_all("Item Price", filters={"price_list": target_price_list}, pluck="item_code")
	)
	copied = skipped_ambiguous = skipped_locked = pending_copies = 0
	for item_code, rows in by_item.items():
		if item_code in existing_items:
			continue
		if len(rows) != 1:
			skipped_ambiguous += 1
			continue
		row = rows[0]
		try:
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": item_code,
					"price_list": target_price_list,
					"price_list_rate": row.price_list_rate,
					"currency": row.currency,
					"uom": row.uom,
					"valid_from": row.valid_from,
					"valid_upto": row.valid_upto,
					"selling": 1,
					"buying": 0,
				}
			).insert(ignore_permissions=True)
		except frappe.QueryTimeoutError:
			frappe.db.rollback()
			copied -= pending_copies
			pending_copies = 0
			skipped_locked += 1
			continue
		copied += 1
		pending_copies += 1
		if pending_copies >= _COMMIT_BATCH_SIZE:
			frappe.db.commit()
			pending_copies = 0
	return copied, skipped_ambiguous, skipped_locked


def apply_initial_pim_catalog(outlet_name, file_url):
	"""Seed missing Foodpanda prices and update public names from one PIM CSV.

	The operation is safe to re-run: it never overwrites an existing Foodpanda
	price and only changes a Shopping Product when a barcode resolves to one
	unique eligible PIM title.
	"""
	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	source_price_list = frappe.db.get_value("Branch", outlet.branch, "default_selling_price_list")
	if not source_price_list:
		frappe.throw(_("Branch {0} has no normal Selling Price List").format(outlet.branch))
	target_price_list = get_or_create_branch_foodpanda_price_list(outlet.branch)
	pim_titles = _pim_titles_by_barcode(file_url)

	products = frappe.get_all(
		"Shopping Product", filters={"enabled": 1}, fields=["name", "item", "public_name"]
	)
	barcodes_by_item = defaultdict(list)
	if products:
		for row in frappe.get_all(
			"Item Barcode",
			filters={"parent": ["in", [product.item for product in products]]},
			fields=["parent", "barcode"],
		):
			if _barcode(row.barcode):
				barcodes_by_item[row.parent].append(row.barcode)

	updated_names = skipped_ambiguous = skipped_locked = pending_name_updates = 0
	for product in products:
		matches = set()
		for barcode in barcodes_by_item[product.item]:
			for variant in _barcode_variants(barcode):
				matches.update(pim_titles.get(variant, set()))
		if len(matches) != 1:
			if matches:
				skipped_ambiguous += 1
			continue
		_title = next(iter(matches))[1]
		if product.public_name != _title:
			try:
				frappe.db.set_value(
					"Shopping Product", product.name, "public_name", _title, update_modified=True
				)
			except frappe.QueryTimeoutError:
				frappe.db.rollback()
				updated_names -= pending_name_updates
				pending_name_updates = 0
				skipped_locked += 1
				continue
			updated_names += 1
			pending_name_updates += 1
			if pending_name_updates >= _COMMIT_BATCH_SIZE:
				frappe.db.commit()
				pending_name_updates = 0

	prices_copied, prices_skipped_ambiguous, prices_skipped_locked = _copy_missing_prices(
		source_price_list, target_price_list
	)
	frappe.db.commit()
	return {
		"outlet": outlet.name,
		"source_price_list": source_price_list,
		"foodpanda_price_list": target_price_list,
		"public_names_updated": updated_names,
		"ambiguous_name_matches_skipped": skipped_ambiguous,
		"locked_name_updates_skipped": skipped_locked,
		"foodpanda_prices_copied": prices_copied,
		"ambiguous_source_prices_skipped": prices_skipped_ambiguous,
		"locked_price_updates_skipped": prices_skipped_locked,
	}
