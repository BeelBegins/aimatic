import frappe
from frappe.utils import flt, getdate


def update_latest_price_incl_taxes(doc, method=None):
	"""Push each submitted line's custom_price_after_taxes onto the Item
	master, and directly onto every existing Item Price row for that item,
	as the latest known incl-tax purchase cost - but only if this document
	is not older than whatever last set it, so a backdated/late submission
	can't clobber a more recent price.

	Item Price's own custom_latest_price_incl_taxes field is declared with
	fetch_from item_code.custom_latest_price_incl_taxes, but fetch_from only
	copies a value once (on create / item_code change) - it is not a live
	link, so already-existing Item Price rows are updated explicitly here
	rather than relying on fetch_from to propagate the change.
	"""
	posting_date = getdate(doc.posting_date)

	for row in doc.items:
		price = flt(row.get("custom_price_after_taxes"))
		if price <= 0:
			continue

		item_code = row.item_code
		current_source_date = frappe.db.get_value("Item", item_code, "custom_latest_price_source_date")
		if current_source_date and getdate(current_source_date) > posting_date:
			continue

		frappe.db.set_value(
			"Item",
			item_code,
			{
				"custom_latest_price_incl_taxes": price,
				"custom_latest_price_source_date": posting_date,
			},
		)

		frappe.db.set_value(
			"Item Price",
			{"item_code": item_code},
			"custom_latest_price_incl_taxes",
			price,
		)
