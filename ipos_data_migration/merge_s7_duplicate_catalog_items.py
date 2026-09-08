"""Merge S7-colliding catalog duplicates into one Item (siezal mock).

Locked pair: Bisconni Chocolate Chip.
- Survivor `STO-ITEM-2026-11166` keeps stock, sales, S7 opening.
- `STO-ITEM-2026-11173` has the later/real barcodes but negative stock.
- After merge the survivor must carry all four barcodes. Do not change
  stock_uom or existing barcode UOMs on the survivor.

Live `szl`: same pair, after backup, BEFORE S7 price/stock so the onhand
file resolves to one item. Do not run this script on szl until TARGET_SITE
is changed and backup is verified.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/merge_s7_duplicate_catalog_items.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import frappe

TARGET_SITE = "szl"

# Survivor = stock + transactions. merge_in = later barcodes / negative stock.
PAIRS = [
	{
		"survivor": "STO-ITEM-2026-11166",
		"merge_in": "STO-ITEM-2026-11173",
		"product": "Bisconni Chocolate Chip Rs=20",
	},
	{
		"survivor": "STO-ITEM-2026-00430",
		"merge_in": "STO-ITEM-2026-09045",
		"product": "Candyland Paradise",
	},
	{
		"survivor": "STO-ITEM-2026-00091",
		"merge_in": "STO-ITEM-2026-00162",
		"product": "7Up 1.5 Ltr",
	},
	{
		"survivor": "STO-ITEM-2026-08009",
		"merge_in": "STO-ITEM-2026-00400",
		"product": "Hilal Jiggles",
	},
	{
		"survivor": "STO-ITEM-2026-09268",
		"merge_in": "STO-ITEM-2026-09267",
		"product": "Kitcy Basil Leaves 15Gm",
	},
	{
		"survivor": "STO-ITEM-2026-16657",
		"merge_in": "STO-ITEM-2026-09957",
		"product": "Dawn Plain Paratha 5Pcs 400Gm",
	},
	{
		"survivor": "STO-ITEM-2026-09208",
		"merge_in": "STO-ITEM-2026-10386",
		"product": "Walls Choc Bar",
	},
]


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")


def _barcodes(item_code):
	return frappe.get_all(
		"Item Barcode",
		filters={"parent": item_code},
		fields=["name", "barcode", "uom", "barcode_type"],
		order_by="idx",
	)


def plan_pair(pair):
	survivor, merge_in = pair["survivor"], pair["merge_in"]
	if not frappe.db.exists("Item", survivor):
		return {**pair, "action": "missing_survivor"}
	if not frappe.db.exists("Item", merge_in):
		return {**pair, "action": "already_merged", "survivor_barcodes": _barcodes(survivor)}
	return {
		**pair,
		"action": "merge",
		"survivor_uom": frappe.db.get_value("Item", survivor, "stock_uom"),
		"merge_uom": frappe.db.get_value("Item", merge_in, "stock_uom"),
		"survivor_barcodes": _barcodes(survivor),
		"merge_barcodes": _barcodes(merge_in),
	}


def _move_barcodes(survivor, merge_in, merge_rows):
	on_survivor = {str(r.barcode).strip() for r in _barcodes(survivor)}
	to_add = []
	for row in merge_rows:
		code = str(row.barcode).strip()
		if not code or code in on_survivor:
			continue
		to_add.append({"barcode": code, "uom": row.uom})
	src = frappe.get_doc("Item", merge_in)
	src.barcodes = []
	src.save(ignore_permissions=True)
	if not to_add:
		return
	doc = frappe.get_doc("Item", survivor)
	stock_uom_before = doc.stock_uom
	old_uom_by_code = {r.barcode: r.uom for r in _barcodes(survivor)}
	try:
		for row in to_add:
			doc.append("barcodes", {"barcode": row["barcode"], "uom": row["uom"] or stock_uom_before})
		doc.save(ignore_permissions=True)
	except Exception:
		restore = frappe.get_doc("Item", merge_in)
		for row in to_add:
			restore.append("barcodes", {"barcode": row["barcode"], "uom": row["uom"]})
		restore.save(ignore_permissions=True)
		raise
	if frappe.db.get_value("Item", survivor, "stock_uom") != stock_uom_before:
		frappe.throw(f"{survivor} stock_uom changed during barcode move")
	after = {r.barcode: r.uom for r in _barcodes(survivor)}
	for code, uom in old_uom_by_code.items():
		if after.get(code) != uom:
			frappe.throw(f"{survivor} existing barcode {code} UOM changed")
	for row in to_add:
		if after.get(row["barcode"]) != (row["uom"] or stock_uom_before):
			frappe.throw(f"{survivor} missing moved barcode {row['barcode']}")


def _drop_colliding_prices(survivor, merge_in):
	survivor_lists = set(
		frappe.get_all("Item Price", filters={"item_code": survivor}, pluck="price_list")
	)
	for name, price_list in frappe.get_all(
		"Item Price",
		filters={"item_code": merge_in},
		fields=["name", "price_list"],
		as_list=True,
	):
		if price_list in survivor_lists:
			frappe.delete_doc("Item Price", name, ignore_permissions=True, force=1)


def apply_pair(plan):
	if plan["action"] != "merge":
		return plan["action"]
	if plan["survivor_uom"] != plan["merge_uom"]:
		frappe.throw(f"stock_uom mismatch {plan['survivor_uom']} vs {plan['merge_uom']}")
	_move_barcodes(plan["survivor"], plan["merge_in"], plan["merge_barcodes"])
	_drop_colliding_prices(plan["survivor"], plan["merge_in"])
	frappe.rename_doc("Item", plan["merge_in"], plan["survivor"], merge=True)
	for name in frappe.get_all(
		"Repost Item Valuation",
		filters={"item_code": plan["survivor"], "status": "Queued"},
		pluck="name",
	):
		frappe.get_doc("Repost Item Valuation", name).repost_now()
	frappe.db.commit()
	return "merged"


def main(apply: bool = False):
	_assert_site()
	plans = [plan_pair(p) for p in PAIRS]
	print({"site": TARGET_SITE, "apply": apply, "plans": plans})
	if not apply:
		print("DRY_RUN — no merge.")
		return plans
	for plan in plans:
		print(plan["product"], apply_pair(plan), "barcodes", _barcodes(plan["survivor"]))
	return plans
