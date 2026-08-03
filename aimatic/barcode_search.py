import frappe
from frappe import _


@frappe.whitelist()
def resolve_item_codes(barcode: str | None = None) -> list[str]:
	"""Return Item parents for an exact Item Barcode value."""
	frappe.has_permission("Item", ptype="read", throw=True)

	barcode = (barcode or "").strip()
	if not barcode:
		frappe.throw(_("Barcode is required"))

	item_codes = frappe.get_all(
		"Item Barcode",
		filters={"barcode": barcode},
		pluck="parent",
		order_by="parent asc, idx asc",
		limit_page_length=100,
	)

	# Preserve query order while protecting the list filter from duplicate child
	# rows if legacy data contains the same barcode more than once.
	return list(dict.fromkeys(item_codes))
