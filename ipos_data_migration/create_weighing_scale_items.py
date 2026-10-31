"""Create weighing-scale (loose, sold by Kg) Items from the Posapplication workbook.

Source: the "Item Import" sheet built by Posapplication
scripts/generate-scale-items.py (docs/weighing-scale/WS-scale-items-import.xlsx).
Columns: ID, Item Name, Item Group, Default Unit of Measure, Maintain Stock,
Allow Sales, Description, FBR Tax Category, Barcode (Barcodes), UOM (Barcodes).

Item master only: no item_defaults, prices or stock (Item Price per Kg is
created by the user afterwards). item_code comes from the STO-ITEM series via
aimatic.item_naming. The Item Barcode is the scale key, e.g. SC29111; the scale
label carries only 29111, so the POS adds the SC prefix before lookup.

Dry-run by default. Refuses to insert while any row is blocked: unknown Item
Group / FBR Tax Category / UOM, or a barcode whose SC key or bare digits already
belong to another Item. Re-running skips rows whose SC barcode already exists on
an Item with the same item_name (idempotent).

User decisions 2026-10-31: FBR Tax Category "Exempt goods"; PLU series 29111+
(10001-style series are too common); WS BAJRA goes to Pulses, where the existing
bajra items are (no "Bajra" Item Group is created).

Run:
    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/create_weighing_scale_items.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"](source)             # dry-run
    ns["main"](source, apply=True) # insert
"""

from __future__ import annotations

from pathlib import Path

import frappe
from openpyxl import load_workbook

ALLOWED_SITES = {"szl", "siezal"}
ITEM_NAMING_SERIES = "STO-ITEM-.YYYY.-"
SHEET = "Item Import"


def _rows(source: str) -> list[dict]:
	if not Path(source).is_file():
		frappe.throw(f"Source file missing: {source}")
	sheet = load_workbook(source, read_only=True, data_only=True)[SHEET]
	rows = [[("" if c is None else str(c).strip()) for c in r] for r in sheet.iter_rows(values_only=True)]
	header = rows[0]
	return [dict(zip(header, r)) for r in rows[1:] if any(r)]


def _plan(source: str):
	ready, skipped, blocked = [], [], []
	seen = set()
	for r in _rows(source):
		name, group, uom = r["Item Name"], r["Item Group"], r["Default Unit of Measure"]
		fbr, barcode = r["FBR Tax Category"], r["Barcode (Barcodes)"]
		digits = barcode[2:] if barcode.upper().startswith("SC") else ""
		reasons = []
		if not frappe.db.exists("Item Group", {"name": group, "is_group": 0}):
			reasons.append(f"item_group_missing:{group}")
		if not frappe.db.get_value("FBR Tax Category", fbr, "enabled"):
			reasons.append(f"fbr_missing_or_disabled:{fbr}")
		if not frappe.db.exists("UOM", uom) or not frappe.db.exists("UOM", r["UOM (Barcodes)"]):
			reasons.append(f"uom_missing:{uom}")
		if not digits.isdigit():
			reasons.append(f"barcode_not_SC_digits:{barcode}")
		if barcode.lower() in seen or name.lower() in seen:
			reasons.append("duplicate_within_file")
		seen.update({barcode.lower(), name.lower()})
		owner = frappe.db.get_value("Item Barcode", {"barcode": barcode}, "parent")
		if owner:
			if frappe.db.get_value("Item", owner, "item_name") == name:
				skipped.append({"barcode": barcode, "item": owner})
				continue
			reasons.append(f"barcode_owned_by:{owner}")
		for key in (digits, digits.lstrip("0")):
			other = frappe.db.get_value("Item Barcode", {"barcode": key}, "parent") or frappe.db.exists("Item", key)
			if key and other:
				reasons.append(f"plu_digits_{key}_owned_by:{other}")
		if frappe.db.exists("Item", {"item_name": name}):
			reasons.append("item_name_exists")
		if reasons:
			blocked.append({"barcode": barcode, "item_name": name, "reasons": reasons})
		else:
			ready.append(r)
	return ready, skipped, blocked


def _create_one(r: dict) -> str:
	item = frappe.new_doc("Item")
	item.naming_series = ITEM_NAMING_SERIES
	item.item_name = r["Item Name"]
	item.description = r["Description"] or r["Item Name"]
	item.item_group = r["Item Group"]
	item.stock_uom = r["Default Unit of Measure"]
	item.is_stock_item = int(r["Maintain Stock"] or 1)
	item.is_sales_item = int(r["Allow Sales"] or 1)
	item.is_purchase_item = 1
	item.custom_fbr_tax_category = r["FBR Tax Category"]
	item.append("barcodes", {"barcode": r["Barcode (Barcodes)"], "uom": r["UOM (Barcodes)"]})
	item.insert(ignore_permissions=True)
	if item.item_defaults:
		item.set("item_defaults", [])
		item.save(ignore_permissions=True)
	return item.name


def main(source: str, apply: bool = False):
	site = getattr(frappe.local, "site", None) or ""
	if site not in ALLOWED_SITES:
		frappe.throw(f"Refusing scale item create on site '{site}'")
	ready, skipped, blocked = _plan(source)
	print({"site": site, "apply": apply, "ready": len(ready), "skipped_existing": len(skipped), "blocked": blocked})
	if blocked:
		print("REFUSING to create: blocked rows remain")
		return {"created": [], "blocked": blocked}
	if not apply:
		print("DRY_RUN -- no inserts. Pass apply=True to create on", site)
		return {"created": [], "ready": len(ready), "dry_run": True}

	created, failed = [], []
	for r in ready:
		try:
			name = _create_one(r)
			frappe.db.commit()
			created.append((name, r["Barcode (Barcodes)"], r["Item Name"]))
		except Exception as exc:
			frappe.db.rollback()
			failed.append({"barcode": r["Barcode (Barcodes)"], "error": str(exc)})
			print(f"FAILED {r['Barcode (Barcodes)']} -> {exc}")
	print({"created": len(created), "failed": failed, "first": created[:2], "last": created[-2:]})
	return {"created": created, "failed": failed}
