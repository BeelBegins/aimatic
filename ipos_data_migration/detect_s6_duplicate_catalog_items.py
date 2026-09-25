"""Read-only: find S6 rows whose Barcode1/Barcode2 resolve to two different
existing Items (candidate catalog duplicates -- same product split across
two Item masters, same situation S7 had 7 signed pairs for).

Not an ERPNext unique-barcode violation (uniqueness is exact string only).
This script only detects and reports candidates with enough context (stock,
sales activity) to judge survivor vs merge-in; it does not merge anything.
Each pair needs its own manual sign-off, same as S7's PAIRS list in
merge_s7_duplicate_catalog_items.py -- do not auto-decide survivor here.

Run against siezal (mock, already has the 4,883 new S6 Items) or szl.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/detect_s6_duplicate_catalog_items.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
"""

from __future__ import annotations

from pathlib import Path

import frappe
import xlrd

ALLOWED_SITES = ("szl",)
SOURCE_XLS = "/home/nabeel/frappe-bench/sites/szl/private/files/1006stockposition.xls"


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site not in ALLOWED_SITES:
		frappe.throw(f"Refusing: expected site in {ALLOWED_SITES}, got '{site}'")
	return site


def _norm_code(v) -> str:
	if v is None:
		return ""
	s = str(v).strip()
	if s.endswith(".0") and s.replace(".", "", 1).isdigit():
		s = s[:-2]
	return s


def _parse_rows() -> list[dict]:
	wb = xlrd.open_workbook(SOURCE_XLS)
	sh = wb.sheet_by_index(0)
	rows = []
	for r in range(1, sh.nrows):
		row = sh.row_values(r)
		if not row or not any(str(c).strip() for c in row):
			continue
		while len(row) < 7:
			row.append("")

		def _s(v):
			if isinstance(v, float) and v == int(v):
				return str(int(v))
			return str(v).strip()

		b1, b2 = _norm_code(_s(row[0])), _norm_code(_s(row[5]))
		if not b1 or not b2:
			continue  # only rows with BOTH barcodes can collide across two items
		rows.append({"Barcode1": b1, "Barcode2": b2, "Description": _s(row[1])})
	return rows


def _variants(code: str) -> set[str]:
	out = {code}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for n in (12, 13, 14):
			out.add(code.zfill(n))
			out.add(stripped.zfill(n))
	return out


def _build_barcode_index():
	index = {}
	for r in frappe.get_all("Item Barcode", fields=["barcode", "parent"]):
		if r.barcode:
			index[str(r.barcode).strip()] = r.parent
	return index


def _resolve(code, index):
	if not code:
		return None
	if code in index:
		return index[code]
	for v in _variants(code):
		if v in index:
			return index[v]
	return None


def _item_activity(item_code: str) -> dict:
	bins = frappe.get_all(
		"Bin", filters={"item_code": item_code}, fields=["warehouse", "actual_qty"], order_by="warehouse"
	)
	total_qty = sum(b.actual_qty for b in bins)
	sle_count = frappe.db.count("Stock Ledger Entry", {"item_code": item_code})
	barcodes = [r.barcode for r in frappe.get_all("Item Barcode", filters={"parent": item_code}, fields=["barcode"])]
	item = frappe.db.get_value(
		"Item", item_code, ["item_name", "stock_uom", "creation"], as_dict=True
	)
	return {
		"item_name": item.item_name if item else None,
		"stock_uom": item.stock_uom if item else None,
		"creation": str(item.creation) if item else None,
		"total_qty": total_qty,
		"nonzero_bins": sum(1 for b in bins if b.actual_qty),
		"sle_count": sle_count,
		"barcode_count": len(barcodes),
		"barcodes": barcodes,
	}


def main():
	site = _assert_site()
	rows = _parse_rows()
	index = _build_barcode_index()

	pairs = {}
	for r in rows:
		item1 = _resolve(r["Barcode1"], index)
		item2 = _resolve(r["Barcode2"], index)
		if item1 and item2 and item1 != item2:
			key = tuple(sorted((item1, item2)))
			pairs.setdefault(key, {"description_samples": set(), "row_count": 0})
			pairs[key]["row_count"] += 1
			pairs[key]["description_samples"].add(r["Description"])

	report = []
	for (a, b), info in pairs.items():
		report.append(
			{
				"item_a": a,
				"item_a_activity": _item_activity(a),
				"item_b": b,
				"item_b_activity": _item_activity(b),
				"row_count": info["row_count"],
				"description_samples": sorted(info["description_samples"])[:5],
			}
		)

	summary = {
		"site": site,
		"source_rows_with_both_barcodes": len(rows),
		"candidate_duplicate_pairs": len(report),
		"pairs": report,
	}
	print(summary)
	return summary
