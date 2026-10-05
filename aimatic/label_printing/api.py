import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime

from aimatic.label_printing.tspl import generate_tspl_for_job
from aimatic.label_printing.utils import build_item_row


def get_default_template(label_type):
	"""Best-guess default template for a label type: the first enabled
	template of that type, if any. Returns None if none are configured."""
	return frappe.db.get_value(
		"AIM Label Template",
		{"label_type": label_type, "enabled": 1},
		"name",
		order_by="creation asc",
	)


_LABEL_TYPE_PRINT_FORMATS = {
	"Barcode Label": "AIM Barcode Label",
	"Shelf Label": "AIM Shelf Label A4",
}


@frappe.whitelist()
def get_base_template_code(label_type):
	"""Return the current Print Format's HTML/CSS/Jinja for a label type, so
	template authors can see what classes/context are already available
	before writing Custom CSS or a new template. Read-only reference, fetched
	live rather than duplicated anywhere."""
	print_format_name = _LABEL_TYPE_PRINT_FORMATS.get(label_type)
	if not print_format_name:
		frappe.throw(_("Unknown label type: {0}").format(label_type))
	return frappe.db.get_value("Print Format", print_format_name, "html") or ""


# Which child-row fields carry quantity/warehouse for each supported source
# doctype - their `items` child table is always fieldname "items" and always
# has item_code/uom/batch_no, but qty and warehouse fieldnames differ.
_SOURCE_TYPE_CONFIG = {
	"Purchase Receipt": {"qty_field": "received_qty", "warehouse_field": "warehouse"},
	# PO labels are often needed before goods arrive; qty is ordered qty.
	"Purchase Order": {"qty_field": "qty", "warehouse_field": "warehouse"},
	"Delivery Note": {"qty_field": "qty", "warehouse_field": "warehouse"},
	"Sales Invoice": {"qty_field": "qty", "warehouse_field": "warehouse"},
	# Material Transfer only: t_warehouse is where the stock (and the items
	# being labeled) actually end up.
	"Stock Entry": {"qty_field": "qty", "warehouse_field": "t_warehouse"},
}

# AIM Label Print Job's own Link field that stores each source type's document.
_SOURCE_LINK_FIELDS = {
	"Purchase Receipt": "purchase_receipt",
	"Purchase Order": "purchase_order",
	"Delivery Note": "delivery_note",
	"Sales Invoice": "sales_invoice",
	"Stock Entry": "stock_entry",
}

SOURCE_CHANGED_SELLING_PRICES = "Changed Selling Prices"

_SOURCE_DESCRIPTION_FIELD = {
	"Purchase Receipt": "supplier",
	"Purchase Order": "supplier",
	"Sales Invoice": "customer",
	"Delivery Note": "customer",
}


def _get_submitted_source_doc(source_type, source_name):
	config = _SOURCE_TYPE_CONFIG.get(source_type)
	if not config:
		frappe.throw(_("Unsupported label source type: {0}").format(source_type))

	doc = frappe.get_doc(source_type, source_name)
	# Purchase Order: draft or submitted (pre-receipt labeling). All other
	# sources stay submitted-only so cancelled/draft stock docs can't label.
	if source_type == "Purchase Order":
		if doc.docstatus == 2:
			frappe.throw(_("Cancelled Purchase Order cannot be used for labeling."))
	elif doc.docstatus != 1:
		frappe.throw(_("{0} {1} must be submitted first.").format(_(source_type), source_name))
	if source_type == "Stock Entry" and doc.purpose != "Material Transfer":
		frappe.throw(_("Only Material Transfer Stock Entries can be used for labeling."))

	return doc, config


def _resolve_price_list(doc, price_list=None):
	"""Priority: an explicitly passed-in price list (e.g. already set on the
	Label Print Job) > the source document's own Selling Price List (Delivery
	Note/Sales Invoice) > the source Branch's selling list > the site-wide
	default Selling Price List. Purchase Receipt/Order/Stock Entry have no
	selling price list of their own, so they fall through to branch/site."""
	if price_list:
		return price_list
	if doc.meta.has_field("selling_price_list") and doc.get("selling_price_list"):
		return doc.get("selling_price_list")
	if doc.get("branch"):
		branch_pl = frappe.db.get_value("Branch", doc.branch, "default_selling_price_list")
		if branch_pl:
			return branch_pl
	return frappe.db.get_single_value("Selling Settings", "selling_price_list")


def _validate_changed_price_window(branch, from_date, to_date):
	branch = (branch or "").strip()
	if not branch:
		frappe.throw(_("Select a Branch."))
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Branch {0} does not exist.").format(branch))
	if not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)

	from_date = getdate(from_date) if from_date else None
	to_date = getdate(to_date) if to_date else None
	if not from_date or not to_date:
		frappe.throw(_("From Date and To Date are required."))
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date."))

	price_list = frappe.db.get_value("Branch", branch, "default_selling_price_list")
	if not price_list:
		frappe.throw(_("Branch {0} has no default Selling Price List.").format(branch))

	return branch, from_date, to_date, price_list


def _get_changed_price_label_rows(branch, from_date, to_date):
	"""Return one current-price shelf-label row per SKU changed in the window.

	Only Rate changes in the branch's normal Selling Price List qualify. This
	deliberately excludes MRP-only and Foodpanda/cost-only changes. When an item
	changed more than once, the current price is printed once using the latest
	change as the audit note.
	"""
	branch, from_date, to_date, price_list = _validate_changed_price_window(
		branch, from_date, to_date
	)
	start = get_datetime(f"{from_date} 00:00:00")
	end = get_datetime(f"{to_date} 23:59:59.999999")
	logs = frappe.get_all(
		"Item Price Update Log",
		filters={
			"branch": branch,
			"price_list": price_list,
			"field_updated": "Rate",
			"update_datetime": ["between", [start, end]],
		},
		fields=["item_code", "old_value", "new_value", "update_datetime"],
		order_by="update_datetime desc, creation desc",
		ignore_permissions=True,
	)

	latest_by_item = {}
	for log in logs:
		latest_by_item.setdefault(log.item_code, log)

	rows = []
	for log in latest_by_item.values():
		row = build_item_row(
			item_code=log.item_code,
			label_type="Shelf Label",
			price_list=price_list,
		)
		row["remarks"] = _("Selling price changed on {0}: {1} → {2}").format(
			str(log.update_datetime)[:19], log.old_value, log.new_value
		)
		rows.append(row)

	rows.sort(key=lambda row: (row.get("item_name") or "", row.get("item_code") or ""))
	return {
		"items": rows,
		"branch": branch,
		"price_list": price_list,
		"price_change_from_date": str(from_date),
		"price_change_to_date": str(to_date),
		"template": get_default_template("Shelf Label"),
		"changed_item_count": len(rows),
	}


@frappe.whitelist()
def get_changed_price_label_items(branch, from_date, to_date):
	"""Load branch shelf-label rows for selling-price changes in a date range."""
	frappe.has_permission("AIM Label Print Job", ptype="create", throw=True)
	return _get_changed_price_label_rows(branch, from_date, to_date)


def _build_rows_from_source_doc(doc, config, label_type, price_list=None):
	"""Build label rows from a source document's items, plus the first
	warehouse found on those items (used as the job's default warehouse)."""
	rows = []
	warehouse = None
	resolved_price_list = _resolve_price_list(doc, price_list)

	for item_row in doc.items:
		qty = item_row.get(config["qty_field"]) or item_row.qty or 1
		extra = {}
		if doc.doctype == "Purchase Receipt":
			extra["purchase_receipt_item"] = item_row.name

		row = build_item_row(
			item_code=item_row.item_code,
			label_type=label_type,
			uom=item_row.uom,
			batch_no=item_row.get("batch_no"),
			price_list=resolved_price_list,
			company=doc.get("company"),
			purchase_qty=qty,
			barcode_label_qty=int(qty) if qty else 1,
			**extra,
		)
		rows.append(row)

		if not warehouse:
			warehouse = item_row.get(config["warehouse_field"])

	if not warehouse and doc.get("set_warehouse"):
		warehouse = doc.set_warehouse

	return rows, warehouse, resolved_price_list


@frappe.whitelist()
def create_from_source_document(source_type, source_name, template=None, price_list=None):
	"""Create a Draft AIM Label Print Job pre-filled from a source document's
	items (Purchase Receipt/Order, Delivery Note, Sales Invoice, or a
	Material Transfer Stock Entry). Does not modify the source document."""
	if source_type == SOURCE_CHANGED_SELLING_PRICES:
		frappe.throw(_("Use the changed-price label loader for this source."))

	frappe.has_permission(source_type, doc=source_name, ptype="read", throw=True)
	frappe.has_permission("AIM Label Print Job", ptype="create", throw=True)

	doc, config = _get_submitted_source_doc(source_type, source_name)
	label_type = "Barcode Label"
	rows, warehouse, resolved_price_list = _build_rows_from_source_doc(doc, config, label_type, price_list)

	job = frappe.new_doc("AIM Label Print Job")
	job.job_title = _("Labels for {0}").format(doc.name)
	job.label_type = label_type
	job.source_type = source_type
	job.set(_SOURCE_LINK_FIELDS[source_type], doc.name)
	job.company = doc.get("company")
	job.warehouse = warehouse
	job.price_list = resolved_price_list
	job.template = template or get_default_template(label_type)

	for row in rows:
		job.append("items", row)

	job.insert()
	return job.name


@frappe.whitelist()
def create_from_purchase_receipt(purchase_receipt, template=None):
	"""Back-compat wrapper for the Purchase Receipt "Create Barcode Labels"
	button - see create_from_source_document."""
	return create_from_source_document("Purchase Receipt", purchase_receipt, template)


@frappe.whitelist()
def get_items_from_source_document(source_type, source_name, label_type="Barcode Label", price_list=None):
	"""Return item rows + company/warehouse/price_list for a source
	document, to populate an AIM Label Print Job form when a source document
	is picked directly on the job (rather than via the source doc's own
	button). Respects a price_list already set on the job if passed in."""
	if source_type == SOURCE_CHANGED_SELLING_PRICES:
		frappe.throw(_("Use the changed-price label loader for this source."))

	frappe.has_permission(source_type, doc=source_name, ptype="read", throw=True)
	doc, config = _get_submitted_source_doc(source_type, source_name)
	rows, warehouse, resolved_price_list = _build_rows_from_source_doc(doc, config, label_type, price_list)
	return {
		"items": rows,
		"company": doc.get("company"),
		"warehouse": warehouse,
		"price_list": resolved_price_list,
	}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def query_source_documents(doctype, txt, searchfield, start, page_len, filters):
	"""Link-field query shared by AIM Label Print Job's source-document
	pickers (Purchase Receipt/Order/Delivery Note/Sales Invoice/Stock Entry):
	latest first, with supplier/customer (or transfer warehouses, for Stock
	Entry) shown as the dropdown description. Purchase Order allows draft +
	submitted; other sources stay submitted-only."""
	base_filters = dict(filters or {})
	if doctype == "Purchase Order":
		base_filters.setdefault("docstatus", ["!=", 2])
	else:
		base_filters["docstatus"] = 1
	if doctype == "Stock Entry":
		base_filters.setdefault("purpose", "Material Transfer")

	description_field = _SOURCE_DESCRIPTION_FIELD.get(doctype)
	fields = ["name"]
	or_filters = [["name", "like", f"%{txt}%"]]
	if description_field:
		fields.append(description_field)
		or_filters.append([description_field, "like", f"%{txt}%"])
	elif doctype == "Stock Entry":
		fields += ["s_warehouse", "t_warehouse"]

	date_field = "transaction_date" if doctype == "Purchase Order" else "posting_date"
	rows = frappe.get_list(
		doctype,
		filters=base_filters,
		or_filters=or_filters,
		fields=fields,
		order_by=f"{date_field} desc, creation desc",
		start=cint(start),
		page_length=cint(page_len),
	)

	results = []
	for row in rows:
		if description_field:
			description = row.get(description_field) or ""
		elif doctype == "Stock Entry":
			description = f"{row.get('s_warehouse') or '?'} -> {row.get('t_warehouse') or '?'}"
		else:
			description = ""
		results.append((row["name"], description))

	return results


@frappe.whitelist()
def search_items(txt=None):
	"""Search items by item code, item name, or barcode for the manual
	item-add dialog on Label Print Job."""
	txt = (txt or "").strip()
	if not txt:
		return []

	like = f"%{txt}%"

	items_by_barcode = frappe.get_all(
		"Item Barcode",
		filters={"barcode": ["like", like]},
		fields=["parent as item_code"],
		limit=20,
	)
	item_codes_from_barcode = [row.item_code for row in items_by_barcode]

	items = frappe.get_all(
		"Item",
		filters={"disabled": 0},
		or_filters=[
			["item_code", "like", like],
			["item_name", "like", like],
			["item_code", "in", item_codes_from_barcode or [""]],
		],
		fields=["item_code", "item_name", "item_group", "stock_uom"],
		limit=20,
	)

	return items


@frappe.whitelist()
def get_item_label_data(
	item_code, label_type="Barcode Label", uom=None, batch_no=None, price_list=None, company=None
):
	"""Resolve barcode/price/dates for one item, used when adding a row
	manually from the Label Print Job form."""
	frappe.has_permission("Item", doc=item_code, ptype="read", throw=True)
	price_list = price_list or frappe.db.get_single_value("Selling Settings", "selling_price_list")
	return build_item_row(
		item_code=item_code,
		label_type=label_type,
		uom=uom,
		batch_no=batch_no,
		price_list=price_list,
		company=company,
	)


@frappe.whitelist()
def check_before_print(label_print_job):
	doc = frappe.get_doc("AIM Label Print Job", label_print_job)
	doc.check_permission("read")
	doc.before_print_check()
	return {"ok": True}


@frappe.whitelist()
def generate_tspl(label_print_job):
	doc = frappe.get_doc("AIM Label Print Job", label_print_job)
	doc.check_permission("read")
	doc.before_print_check()
	return generate_tspl_for_job(doc)


@frappe.whitelist()
def mark_printed(label_print_job):
	doc = frappe.get_doc("AIM Label Print Job", label_print_job)
	doc.check_permission("write")
	doc.db_set("status", "Printed")
	if doc.source_type == SOURCE_CHANGED_SELLING_PRICES and doc.replacement_status == "Not Applicable":
		doc.db_set("replacement_status", "Pending Replacement")
	return {"ok": True}


@frappe.whitelist()
def mark_replacement_confirmed(label_print_job):
	doc = frappe.get_doc("AIM Label Print Job", label_print_job)
	doc.check_permission("write")
	if doc.source_type != SOURCE_CHANGED_SELLING_PRICES:
		frappe.throw(_("Replacement confirmation is only available for changed-price labels."))
	if doc.status != "Printed":
		frappe.throw(_("Print the changed-price labels before confirming shelf replacement."))
	doc.db_set("replacement_status", "Replacement Confirmed")
	doc.db_set("replacement_confirmed_by", frappe.session.user)
	doc.db_set("replacement_confirmed_on", now_datetime())
	return {"ok": True}
