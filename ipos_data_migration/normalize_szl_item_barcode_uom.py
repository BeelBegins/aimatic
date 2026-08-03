"""Normalize SZL Item and Item Barcode UOM metadata to Pcs."""

import frappe


TARGET_SITE = "szl"
STOCK_UOM = "Pcs"
DRY_RUN = True


def run():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(f"This script is locked to {TARGET_SITE}, not {frappe.local.site}.")
	if not frappe.db.exists("UOM", STOCK_UOM):
		frappe.throw(f"UOM {STOCK_UOM} does not exist.")

	item_exceptions = frappe.db.count("Item", {"stock_uom": ["!=", STOCK_UOM]})
	blank_item_uom = frappe.db.count("Item", {"stock_uom": ["in", ["", None]]})
	barcode_exceptions = frappe.db.sql(
		"""select count(*) from `tabItem Barcode`
		where coalesce(uom, '') != %s""",
		STOCK_UOM,
	)[0][0]

	print(f"DRY_RUN={DRY_RUN}")
	print(f"Items requiring Stock UOM repair: {item_exceptions + blank_item_uom}")
	print(f"Barcodes requiring UOM repair: {barcode_exceptions}")

	if DRY_RUN:
		return

	if item_exceptions or blank_item_uom:
		frappe.throw(
			"Item Stock UOM exceptions exist. Review them before changing stock metadata."
		)

	frappe.db.sql(
		"""update `tabItem Barcode`
		set uom = %s, modified = now(), modified_by = %s
		where coalesce(uom, '') != %s""",
		(STOCK_UOM, frappe.session.user, STOCK_UOM),
	)
	frappe.db.commit()
	print(f"Updated barcode rows: {barcode_exceptions}")
