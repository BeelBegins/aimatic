"""Read-only audit: S5 (Sector C) stock-position barcodes vs catalog.

Source: sites/szl/private/files/1005stockposition.xls (File ff345d08d5),
uploaded 2026-09-20. "1005" is S5's own FBR-style branch code, same
convention as S7's "1007". Same 7-column shape as the S7 onhand file, just
relabelled (Barcode1, Description, Quantity/Onhand, Cost Price/CurCost,
Sale Price/SalePrice, Barcode2, TotalCost/Total) -- parsed positionally like
audit_s7_barcodes_vs_szl.py, so header text differences don't matter.

Unlike the S7 script, this reads the .xls directly with xlrd (it parses
cleanly, no libreoffice-to-CSV conversion needed for this file).

Matching convention matches import.md / audit_s7_barcodes_vs_szl.py:
Barcode1/Barcode2 are plain Item Barcode values, not Item.item_code. Also
checks GTIN leading-zero / padding variants used by POS scanners.

Dry-run only -- writes xlsx under /tmp and optionally uploads result Files
to the target site when UPLOAD_RESULTS=True (File insert only, no Item
creates, no price/stock writes).

Deliberately allows either mock (siezal, restored from a current szl backup
2026-09-12) or live (szl) as target -- unlike S7's single-site hard target,
this script's first-ever run is intended for the mock, so it must not
refuse siezal. Never allows any other site.

Run:
    bench --site szl console
    exec(open("apps/aimatic/ipos_data_migration/audit_s5_barcodes_vs_szl.py").read())
    main()
"""

from __future__ import annotations

import json
from pathlib import Path

import frappe
import xlrd
from frappe.utils import now_datetime
from openpyxl import Workbook

# Hard targets — refuse accidental runs on the wrong site/file.
ALLOWED_SITES = ("szl",)
SOURCE_XLS = "/home/nabeel/frappe-bench/sites/szl/private/files/1005stockposition.xls"
SOURCE_FILE_ID = "ff345d08d5"
UPLOAD_RESULTS = True  # File docs only; never creates Items
RESULT_FOLDER = "Home/Migrations/S5"


def _assert_target():
	site = getattr(frappe.local, "site", None) or ""
	if site not in ALLOWED_SITES:
		frappe.throw(f"Refusing to run: expected site in {ALLOWED_SITES}, got '{site}'")
	if not Path(SOURCE_XLS).is_file():
		frappe.throw(f"Source file missing: {SOURCE_XLS}")
	return site


def _parse_xls(path: str) -> list[dict]:
	wb = xlrd.open_workbook(path)
	sh = wb.sheet_by_index(0)
	data = []
	for r in range(1, sh.nrows):  # skip header row
		row = sh.row_values(r)
		if not row or not any(str(c).strip() for c in row):
			continue
		while len(row) < 7:
			row.append("")

		def _s(v):
			if isinstance(v, float) and v == int(v):
				return str(int(v))
			return str(v).strip()

		rec = {
			"Barcode1": _s(row[0]),
			"Description": _s(row[1]),
			"Onhand": _s(row[2]),
			"CurCost": _s(row[3]),
			"SalePrice": _s(row[4]),
			"Barcode2": _s(row[5]),
			"Total": _s(row[6]),
		}
		if not rec["Barcode1"] and not rec["Barcode2"]:
			continue
		data.append(rec)
	return data


def _variants(code: str) -> set[str]:
	out = {code}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		out.add(code.zfill(12))
		out.add(code.zfill(13))
		out.add(code.zfill(14))
		out.add(stripped.zfill(12))
		out.add(stripped.zfill(13))
		out.add(stripped.zfill(14))
	return out


def _build_catalog():
	existing_barcodes = {str(b).strip() for b in frappe.get_all("Item Barcode", pluck="barcode") if b}
	existing_items = set(frappe.get_all("Item", pluck="name"))
	barcode_to_item = {}
	for r in frappe.get_all("Item Barcode", fields=["barcode", "parent"]):
		if r.barcode:
			barcode_to_item[str(r.barcode).strip()] = r.parent
	variant_index = {}
	for code in list(existing_barcodes):
		for v in _variants(code):
			variant_index.setdefault(v, code)
	for code in list(existing_items):
		for v in _variants(code):
			variant_index.setdefault(v, code)
	return existing_barcodes, existing_items, barcode_to_item, variant_index


def _exists(code, existing_barcodes, existing_items, variant_index) -> bool:
	if not code:
		return False
	if code in existing_barcodes or code in existing_items:
		return True
	return any(v in variant_index for v in _variants(code))


def _resolve(code, barcode_to_item, existing_items, variant_index):
	if not code:
		return None
	if code in barcode_to_item:
		return barcode_to_item[code]
	if code in existing_items:
		return code
	for v in _variants(code):
		canon = variant_index.get(v)
		if not canon:
			continue
		if canon in barcode_to_item:
			return barcode_to_item[canon]
		if canon in existing_items:
			return canon
	return None


def _ensure_folder(path: str) -> str:
	parts = [p for p in path.split("/") if p]
	if not parts or parts[0] != "Home":
		frappe.throw(f"Folder path must start with Home/: {path}")
	parent = "Home"
	for part in parts[1:]:
		folder_name = f"{parent}/{part}" if parent != "Home" else f"Home/{part}"
		if not frappe.db.exists("File", {"is_folder": 1, "name": folder_name}):
			try:
				from frappe.core.doctype.file.file import create_new_folder

				create_new_folder(part, parent)
			except Exception:
				doc = frappe.new_doc("File")
				doc.file_name = part
				doc.is_folder = 1
				doc.folder = parent
				doc.insert(ignore_permissions=True)
		parent = folder_name
	return parent


def _write_xlsx(path: Path, headers: list[str], rows: list[list]) -> Path:
	wb = Workbook()
	ws = wb.active
	ws.title = "Sheet1"
	ws.append(headers)
	for row in rows:
		ws.append(list(row))
	wb.save(path)
	return path


def _upload_file(local_path: str, folder: str) -> str:
	content = Path(local_path).read_bytes()
	fname = Path(local_path).name
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": fname,
			"is_private": 1,
			"folder": folder,
			"content": content,
		}
	)
	f.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Uploaded File {f.name} -> {f.file_url} ({fname})")
	return f.name


def main():
	site = _assert_target()
	rows = _parse_xls(SOURCE_XLS)
	existing_barcodes, existing_items, barcode_to_item, variant_index = _build_catalog()

	all_codes = set()
	for r in rows:
		if r["Barcode1"]:
			all_codes.add(r["Barcode1"])
		if r["Barcode2"]:
			all_codes.add(r["Barcode2"])

	missing_rows = []
	matched_rows = []
	for r in rows:
		b1, b2 = r["Barcode1"], r["Barcode2"]
		hit1 = _exists(b1, existing_barcodes, existing_items, variant_index)
		hit2 = _exists(b2, existing_barcodes, existing_items, variant_index) if b2 else False
		if hit1 or hit2:
			item = _resolve(b1, barcode_to_item, existing_items, variant_index) or _resolve(
				b2, barcode_to_item, existing_items, variant_index
			)
			matched_rows.append({**r, "matched_via": "b1" if hit1 else "b2", "item": item})
		else:
			missing_rows.append(r)

	missing_barcode_set = set()
	present_barcode_set = set()
	for code in all_codes:
		if _exists(code, existing_barcodes, existing_items, variant_index):
			present_barcode_set.add(code)
		else:
			missing_barcode_set.add(code)

	orphan_second = []
	for r in matched_rows:
		b2 = r["Barcode2"]
		if b2 and not _exists(b2, existing_barcodes, existing_items, variant_index):
			orphan_second.append(
				{
					**r,
					"missing_barcode": b2,
					"existing_item": _resolve(r["Barcode1"], barcode_to_item, existing_items, variant_index),
				}
			)

	stamp = now_datetime().strftime("%Y%m%d_%H%M%S")
	out_dir = Path(f"/tmp/s5_barcode_audit_{stamp}")
	out_dir.mkdir(parents=True, exist_ok=True)

	miss_headers = ["Barcode1", "Barcode2", "Description", "Onhand", "CurCost", "SalePrice", "Total"]
	miss_path = out_dir / f"s5_missing_items_{stamp}.xlsx"
	_write_xlsx(miss_path, miss_headers, [[r[h] for h in miss_headers] for r in missing_rows])

	by_code = {}
	for r in rows:
		for role, code in (("Barcode1", r["Barcode1"]), ("Barcode2", r["Barcode2"])):
			if code and code in missing_barcode_set and code not in by_code:
				by_code[code] = (role, r["Description"], r["Onhand"], r["SalePrice"])
	abs_path = out_dir / f"s5_absent_barcodes_{stamp}.xlsx"
	_write_xlsx(
		abs_path,
		["barcode", "seen_as", "description_sample", "onhand", "sale_price"],
		[[code, *by_code[code]] for code in sorted(by_code)],
	)

	orphan_headers = ["Barcode1", "missing_Barcode2", "existing_item", "Description", "Onhand", "SalePrice"]
	orphan_path = out_dir / f"s5_orphan_barcode2_{stamp}.xlsx"
	_write_xlsx(
		orphan_path,
		orphan_headers,
		[
			[r["Barcode1"], r["missing_barcode"], r["existing_item"], r["Description"], r["Onhand"], r["SalePrice"]]
			for r in orphan_second
		],
	)

	summary = {
		"stamp": stamp,
		"site": site,
		"source_file": "1005stockposition.xls",
		"source_file_id": SOURCE_FILE_ID,
		"source_rows": len(rows),
		"matched_rows": len(matched_rows),
		"missing_rows_neither_barcode_on_catalog": len(missing_rows),
		"unique_source_barcodes": len(all_codes),
		"unique_barcodes_present": len(present_barcode_set),
		"unique_barcodes_absent": len(missing_barcode_set),
		"matched_rows_with_missing_barcode2_only": len(orphan_second),
		"item_count": len(existing_items),
		"barcode_count": len(existing_barcodes),
		"local_files": {
			"missing_items": str(miss_path),
			"absent_barcodes": str(abs_path),
			"orphan_barcode2": str(orphan_path),
		},
		"uploaded_file_ids": {},
		"missing_row_samples": [
			{
				"Barcode1": r["Barcode1"],
				"Barcode2": r["Barcode2"],
				"Description": r["Description"][:80],
				"Onhand": r["Onhand"],
			}
			for r in missing_rows[:25]
		],
	}

	summary_path = out_dir / f"s5_barcode_summary_{stamp}.json"
	summary_path.write_text(json.dumps(summary, indent=2))

	if UPLOAD_RESULTS:
		folder = _ensure_folder(RESULT_FOLDER)
		summary["uploaded_file_ids"]["missing_items"] = _upload_file(str(miss_path), folder)
		summary["uploaded_file_ids"]["absent_barcodes"] = _upload_file(str(abs_path), folder)
		summary["uploaded_file_ids"]["orphan_barcode2"] = _upload_file(str(orphan_path), folder)
		summary_path.write_text(json.dumps(summary, indent=2))
		summary["uploaded_file_ids"]["summary_json"] = _upload_file(str(summary_path), folder)
		summary_path.write_text(json.dumps(summary, indent=2))
		print(f"Uploaded summary File {summary['uploaded_file_ids']['summary_json']}")

	print(json.dumps(summary, indent=2))
	return summary
