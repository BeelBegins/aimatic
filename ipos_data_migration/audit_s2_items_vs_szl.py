"""Read-only audit: S2 (Bhatta Chowk) item master + onhand file vs szl catalog.

Source: sites/szl/private/files/s2ItemMasterho.xlsx. One file carries both the
item master columns and S2's own stock/price columns (same shape as the S1
file in import.md): ItemCode=barcode 1, RefCode=barcode 2, Onhand=stock qty,
CurCost=tax-INCLUSIVE cost, Slprice=selling price, MRP, rp, Fbr_Tax_Category.

Writes nothing. Reports: source rows, rows matched to an existing szl Item by
barcode (GTIN leading-zero variants tried, like audit_s6), rows missing, and for
the missing rows whether SubCatName / Fbr_Tax_Category resolve to EXISTING Item
Groups / FBR Tax Categories (nothing is auto-created -- ipos-migration gates).

Run:
    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/audit_s2_items_vs_szl.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import frappe
from openpyxl import load_workbook

TARGET_SITE = "szl"
SOURCE = "/home/nabeel/frappe-bench/sites/szl/private/files/s2ItemMasterho.xlsx"
JUNK_SUBCATS = {"", "NULL", "TESTING"}
# User-approved aliases (2026-10-11). Same three as S6 plus the Colour spelling.
GROUP_ALIASES = {
	"COLOR COSMATICS": "Colour Cosmetics",
	"ELECTONIC ITEMS": "Electronic Items",
	"HOUSEHOLD SUNDRIES": "Household Essentials",
}


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected site '{TARGET_SITE}', got '{site}'")
	if not Path(SOURCE).is_file():
		frappe.throw(f"Source file missing: {SOURCE}")


def _norm(v) -> str:
	if v is None:
		return ""
	s = str(v).strip()
	if s.endswith(".0") and s.replace(".", "", 1).isdigit():
		s = s[:-2]
	return "" if s.upper() == "NULL" else s


def _num(v) -> float:
	try:
		return float(str(v or "").strip() or 0)
	except ValueError:
		return 0.0


def _variants(code: str) -> set[str]:
	out = {code}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for n in (12, 13, 14):
			out.add(code.zfill(n))
			out.add(stripped.zfill(n))
	return out


def _rows():
	ws = load_workbook(SOURCE, read_only=True, data_only=True).active
	it = ws.iter_rows(values_only=True)
	header = [str(h).strip() if h is not None else "" for h in next(it)]
	for row in it:
		if not any(v is not None for v in row):
			continue
		yield {header[i]: (row[i] if i < len(row) else None) for i in range(len(header))}


def main():
	_assert_site()
	catalog = {}
	for r in frappe.get_all("Item Barcode", fields=["barcode", "parent"]):
		if r.barcode:
			for v in _variants(str(r.barcode).strip()):
				catalog.setdefault(v, r.parent)
	for name in frappe.get_all("Item", pluck="name"):
		for v in _variants(name):
			catalog.setdefault(v, name)

	def resolve(code):
		for v in _variants(code):
			if v in catalog:
				return catalog[v]
		return None

	groups = {}
	for g in frappe.get_all("Item Group", fields=["name", "item_group_name"]):
		for key in (g.name, g.item_group_name):
			if key:
				groups.setdefault(str(key).lower(), g.name)
	fbr = {n.lower(): n for n in frappe.get_all("FBR Tax Category", pluck="name")}

	c = Counter()
	unknown_group, unknown_fbr = Counter(), Counter()
	so_unknown_group, so = Counter(), Counter()
	item_rows = {}
	held_multi_rows = set()
	multi = []  # source rows whose two barcodes resolve to two different Items
	seen_items = Counter()
	stock = {"matched": [0, 0.0], "missing": [0, 0.0]}
	for r in _rows():
		c["rows"] += 1
		b1, b2 = _norm(r.get("ItemCode")), _norm(r.get("RefCode"))
		i1 = resolve(b1) if b1 else None
		i2 = resolve(b2) if b2 else None
		onhand = _num(r.get("Onhand"))
		if i1 and i2 and i1 != i2:
			multi.append((b1, b2, i1, i2))
		item = i1 or i2
		if item:
			item_rows.setdefault(item, []).append(onhand)
		if i1 and i2 and i1 != i2 and onhand:
			so["held_two_item_conflict_with_stock"] += 1
		if item:
			c["matched"] += 1
			seen_items[item] += 1
			if i2 is None and b2:
				c["matched_orphan_barcode2"] += 1
			bucket = stock["matched"]
		else:
			c["missing"] += 1
			bucket = stock["missing"]
			sub = _norm(r.get("SubCatName"))
			if onhand:
				so["missing_with_stock"] += 1
				so["missing_with_stock_neg" if onhand < 0 else "missing_with_stock_pos"] += 1
				alias = GROUP_ALIASES.get(sub.upper(), sub)
				if sub.upper() in JUNK_SUBCATS:
					so["missing_with_stock_junk_subcat"] += 1
				elif alias.lower() not in groups:
					so_unknown_group[sub] += 1
			else:
				so["missing_zero_stock_ignored"] += 1
			if sub.upper() in JUNK_SUBCATS:
				c["missing_junk_subcat"] += 1
			elif sub.lower() not in groups:
				unknown_group[sub] += 1
			cat = _norm(r.get("Fbr_Tax_Category"))
			if not cat or cat.lower() not in fbr:
				unknown_fbr[cat or "(blank)"] += 1
		if onhand:
			bucket[0] += 1
			bucket[1] += onhand
		if not b1:
			c["blank_itemcode"] += 1
	so["held_double_hit_items_with_stock"] = sum(
		1 for v in item_rows.values() if len(v) > 1 and any(v)
	)
	so["held_double_hit_rows_with_stock"] = sum(
		sum(1 for x in v if x) for v in item_rows.values() if len(v) > 1 and any(v)
	)
	c["file_rows_hitting_same_existing_item_twice"] = sum(1 for v in seen_items.values() if v > 1)
	result = {
		"site": TARGET_SITE,
		"counters": dict(c),
		"stock_rows_qty_matched": stock["matched"],
		"stock_rows_qty_missing": stock["missing"],
		"two_barcodes_two_different_items": len(multi),
		"multi_sample": multi[:5],
		"missing_unknown_item_group": dict(unknown_group.most_common(30)),
		"missing_unknown_or_blank_fbr": dict(unknown_fbr),
		"stock_only_plan": dict(so),
		"stock_only_unknown_item_group": dict(so_unknown_group.most_common()),
		"szl_items": frappe.db.count("Item"),
	}
	print(result)
	return result
