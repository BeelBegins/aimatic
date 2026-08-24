import frappe
from frappe import _
from frappe.utils import flt


def compute_shelf_gm_percent(shelf_price, cost_after_taxes):
	"""Gross margin % on Sale Price vs Price After Taxes.

	Matches purchase_receipt_custom_layout: (sale - cost) / sale * 100.
	Returns 0 when Sale Price is blank/zero so KPOs see a clear empty state
	rather than a divide-by-zero.
	"""
	sale = flt(shelf_price)
	if sale <= 0:
		return 0.0
	return flt((sale - flt(cost_after_taxes)) / sale * 100, 2)


def compute_shelf_price_from_gm(cost_after_taxes, gm_percent, round_whole=True):
	"""Invert GM % into Sale Price: sale = cost / (1 - gm/100).

	KPOs typically enter whole margins (20 / 15 / 10) then round the
	resulting shelf price to the nearest rupee. Returns None when GM % is
	blank or >= 100 (would divide by zero / go non-positive).
	"""
	gm = flt(gm_percent)
	cost = flt(cost_after_taxes)
	if gm >= 100:
		return None
	denom = 1 - (gm / 100)
	if abs(denom) < 1e-12:
		return None
	sale = cost / denom
	if round_whole:
		return flt(round(sale))
	return flt(sale, 2)


def set_shelf_gm_percent(doc, method=None):
	"""Keep GM % in sync with final Sale Price vs Price After Taxes.

	Desk entry can drive Sale Price from an edited GM % (client); on save
	we recompute GM % from the stored prices so print/layout stay honest
	after whole-rupee rounding. Skip submitted docs: older receipts still
	store 0, and mutating GM% on Update hits allow_on_submit=0.
	"""
	if getattr(doc, "docstatus", 0) == 1:
		return

	for row in doc.items:
		row.custom_gm_percent = compute_shelf_gm_percent(
			row.custom_shelf_price,
			row.custom_price_after_taxes,
		)


def reset_price_update_status_on_amend(doc, method=None):
	"""Desk amend copies no_copy fields (frappe.model.copy_doc with
	from_amend=true skips the no_copy gate). An amended Purchase Receipt
	must not inherit Updated/Skipped or the submit dialog / retry buttons
	will no-op and leave shelf/Foodpanda prices stale.
	"""
	if not getattr(doc, "amended_from", None):
		return

	doc.custom_branch_price_update_status = "Pending"
	doc.custom_foodpanda_price_update_status = "Pending"


def validate_shelf_price_before_submit(doc, method=None):
	"""Shelf Price must never undercut cost. Only enforced for rows where a
	shelf price was actually entered - the field isn't mandatory, and many
	Purchase Receipt rows (e.g. non-retail restock) never carry one.
	"""
	for row in doc.items:
		shelf_price = flt(row.get("custom_shelf_price"))
		if shelf_price <= 0:
			continue

		cost = flt(row.get("custom_price_after_taxes"))
		if shelf_price < cost:
			frappe.throw(
				_(
					"Row #{0}: Shelf Price ({1}) cannot be less than Cost After Taxes ({2}) for item {3}"
				).format(row.idx, shelf_price, cost, row.item_code)
			)


def restore_prices_on_cancel(doc, method=None):
	"""Undo shelf_pricing's own Item Price / Item.custom_mrp writes for this
	receipt, but only where nothing more recent has since overwritten them -
	mirrors item_pricing's "does the current state still match what I last
	wrote" gate so cancelling an old Purchase Receipt can never clobber a
	newer price update from a later one.
	"""
	logs = frappe.get_all(
		"Item Price Update Log",
		filters={"purchase_receipt": doc.name},
		fields=["name", "item_code", "price_list", "field_updated", "old_value", "new_value"],
	)
	if not logs:
		return

	for log in logs:
		if log.price_list:
			_restore_item_price_field(log)
		else:
			_restore_item_mrp(log)


def _restore_item_price_field(log):
	fieldname = "price_list_rate" if log.field_updated == "Rate" else "custom_mrp"
	item_price_name = frappe.db.get_value(
		"Item Price", {"item_code": log.item_code, "price_list": log.price_list, "selling": 1}, "name"
	)
	if not item_price_name:
		return

	current_value = flt(frappe.db.get_value("Item Price", item_price_name, fieldname))
	if current_value != flt(log.new_value):
		# Superseded by a later update - leave it alone.
		return

	frappe.db.set_value("Item Price", item_price_name, fieldname, log.old_value)


def _restore_item_mrp(log):
	current_value = flt(frappe.db.get_value("Item", log.item_code, "custom_mrp"))
	if current_value != flt(log.new_value):
		return

	frappe.db.set_value("Item", log.item_code, "custom_mrp", log.old_value)
