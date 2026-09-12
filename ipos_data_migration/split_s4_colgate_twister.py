"""Split Colgate Twister M from the combined Premier Clean catalog Item.

S4's source and master files confirm that barcodes 8886950092133 and
8886950093352 are different toothbrushes. The shared catalog currently puts
both on ``STO-ITEM-2026-09751`` (Colgate Premier Clean Tooth Brush).

This script is hard-targeted to the ``siezal`` mock. It keeps the Premier
barcode on the existing Item and moves the Twister barcode to one new Item.
It does not move stock, prices, or transactions from S1/S7; physical counts
are required before the equivalent live split.

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/split_s4_colgate_twister.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)  # approved siezal mock split
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import frappe

TARGET_SITE = "siezal"
MASTER_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/Ho-MasterItemFiled5b674.xlsx"
MASTER_SHA256 = "a91f2be7c1b8b09e2dd3d9040b02172d403a34b32847ffb3a087ae4e54b50f98"
SOURCE_ITEM = "STO-ITEM-2026-09751"
SOURCE_ITEM_NAME = "Colgate Premier Clean Tooth Brush"
PREMIER_BARCODE = "8886950092133"
TWISTER_BARCODE = "8886950093352"
TWISTER_ITEM_NAME = "Colgate Twister M"
ITEM_GROUP = "T-Paste T-Brush - Mouth Wash"
BRAND = "Colgate"
FBR_TAX_CATEGORY = "3rd Schedule Goods"
MRP = 140
STOCK_UOM = "Pcs"


def _assert_target():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing Colgate split: expected {TARGET_SITE}, got {site}")
	path = Path(MASTER_PATH)
	if not path.is_file():
		frappe.throw(f"S4 master missing: {MASTER_PATH}")
	digest = hashlib.sha256(path.read_bytes()).hexdigest()
	if digest != MASTER_SHA256:
		frappe.throw(f"S4 master hash changed: expected {MASTER_SHA256}, got {digest}")
	if not frappe.db.exists("Item Group", ITEM_GROUP):
		frappe.throw(f"Missing approved Item Group {ITEM_GROUP}")
	if not frappe.db.exists("Brand", BRAND):
		frappe.throw(f"Missing approved Brand {BRAND}")
	if not frappe.db.exists("FBR Tax Category", FBR_TAX_CATEGORY):
		frappe.throw(f"Missing approved FBR Tax Category {FBR_TAX_CATEGORY}")


def _barcode_owner(barcode):
	owners = frappe.get_all("Item Barcode", filters={"barcode": barcode}, pluck="parent")
	if len(owners) > 1:
		frappe.throw(f"Barcode {barcode} has multiple owners: {owners}")
	return owners[0] if owners else None


def _source_activity():
	return {
		"nonzero_bins": frappe.db.sql(
			"SELECT COUNT(*) FROM tabBin WHERE item_code=%s AND actual_qty!=0", SOURCE_ITEM
		)[0][0],
		"bin_qty": float(
			frappe.db.sql(
				"SELECT COALESCE(SUM(actual_qty),0) FROM tabBin WHERE item_code=%s", SOURCE_ITEM
			)[0][0]
			or 0
		),
		"sle_count": frappe.db.sql(
			"SELECT COUNT(*) FROM `tabStock Ledger Entry` WHERE item_code=%s AND is_cancelled=0",
			SOURCE_ITEM,
		)[0][0],
		"price_count": frappe.db.count("Item Price", {"item_code": SOURCE_ITEM}),
	}


def _verify_existing_target(item_code):
	doc = frappe.get_doc("Item", item_code)
	errors = []
	for field, expected in {
		"item_name": TWISTER_ITEM_NAME,
		"item_group": ITEM_GROUP,
		"brand": BRAND,
		"custom_fbr_tax_category": FBR_TAX_CATEGORY,
		"stock_uom": STOCK_UOM,
	}.items():
		if doc.get(field) != expected:
			errors.append(f"{field}={doc.get(field)!r}, expected {expected!r}")
	if float(doc.custom_mrp or 0) != MRP:
		errors.append(f"custom_mrp={doc.custom_mrp!r}, expected {MRP}")
	if doc.item_defaults:
		errors.append("unexpected item_defaults rows")
	if errors:
		frappe.throw(f"Existing Twister Item {item_code} failed verification: {errors}")
	return doc


def main(apply: bool = False):
	_assert_target()
	source = frappe.get_doc("Item", SOURCE_ITEM)
	if source.item_name != SOURCE_ITEM_NAME:
		frappe.throw(f"Unexpected source Item name: {source.item_name}")
	if _barcode_owner(PREMIER_BARCODE) != SOURCE_ITEM:
		frappe.throw(f"Premier barcode is not owned by {SOURCE_ITEM}")

	twister_owner = _barcode_owner(TWISTER_BARCODE)
	activity_before = _source_activity()
	if twister_owner and twister_owner != SOURCE_ITEM:
		target = _verify_existing_target(twister_owner)
		result = {
			"site": TARGET_SITE,
			"apply": apply,
			"status": "already_applied",
			"source_item": SOURCE_ITEM,
			"twister_item": target.name,
			"source_activity_unchanged": activity_before,
		}
		print(result)
		return result
	if twister_owner != SOURCE_ITEM:
		frappe.throw(f"Twister barcode has unexpected owner: {twister_owner}")

	name_collisions = frappe.get_all(
		"Item", filters={"item_name": TWISTER_ITEM_NAME}, pluck="name"
	)
	if name_collisions:
		frappe.throw(f"Twister-named Item already exists without its barcode: {name_collisions}")

	plan = {
		"site": TARGET_SITE,
		"apply": apply,
		"source_item": SOURCE_ITEM,
		"keep_barcode": PREMIER_BARCODE,
		"new_item_name": TWISTER_ITEM_NAME,
		"move_barcode": TWISTER_BARCODE,
		"item_group": ITEM_GROUP,
		"brand": BRAND,
		"fbr_tax_category": FBR_TAX_CATEGORY,
		"mrp": MRP,
		"source_activity_not_moved": activity_before,
	}
	print(plan)
	if not apply:
		print("DRY_RUN — no Item or barcode changes.")
		return plan

	source.set(
		"barcodes",
		[row for row in source.barcodes if str(row.barcode or "").strip() != TWISTER_BARCODE],
	)
	if not any(str(row.barcode or "").strip() == PREMIER_BARCODE for row in source.barcodes):
		frappe.throw("Premier barcode would be lost from the source Item")
	source.save(ignore_permissions=True)

	target = frappe.new_doc("Item")
	target.naming_series = "STO-ITEM-.YYYY.-"
	target.item_name = TWISTER_ITEM_NAME
	target.description = TWISTER_ITEM_NAME
	target.item_group = ITEM_GROUP
	target.brand = BRAND
	target.custom_fbr_tax_category = FBR_TAX_CATEGORY
	target.custom_mrp = MRP
	target.stock_uom = STOCK_UOM
	target.is_stock_item = 1
	target.is_sales_item = 1
	target.is_purchase_item = 1
	target.has_variants = 0
	target.disabled = 0
	target.append("barcodes", {"barcode": TWISTER_BARCODE, "uom": STOCK_UOM})
	target.insert(ignore_permissions=True)

	_verify_existing_target(target.name)
	if _barcode_owner(PREMIER_BARCODE) != SOURCE_ITEM:
		frappe.throw("Premier barcode verification failed")
	if _barcode_owner(TWISTER_BARCODE) != target.name:
		frappe.throw("Twister barcode verification failed")
	if _source_activity() != activity_before:
		frappe.throw("Source Item stock/ledger/price activity changed during catalog split")
	if frappe.db.count("Bin", {"item_code": target.name}):
		frappe.throw("New Twister Item unexpectedly has Bin rows")
	if frappe.db.count("Item Price", {"item_code": target.name}):
		frappe.throw("New Twister Item unexpectedly has Item Prices")
	if frappe.db.count("Stock Ledger Entry", {"item_code": target.name}):
		frappe.throw("New Twister Item unexpectedly has Stock Ledger Entries")

	frappe.db.commit()
	result = {
		"site": TARGET_SITE,
		"status": "applied",
		"source_item": SOURCE_ITEM,
		"twister_item": target.name,
		"source_activity_unchanged": activity_before,
	}
	print(result)
	return result
