"""Read-only audit: S7 Itemonhand barcodes vs szl Item Barcode catalog.

Source: sites/szl/private/files/1007.xls (File c893bbf6c2), uploaded
2026-09-06 -- replaces the 2026-09-04 Itemonhand-S7 Store.xls (File
0c7a4cfbc9), which is stale (fresh source confirmed by the user 2026-09-06).
Same column order (Barcode1, Description, Onhand/qty, CurCost/costprice,
SalePrice/saleprice, Barcode2, Total/TotalCost) -- _parse_csv is positional,
not header-name based, so the header text change doesn't matter.

Matching convention matches import.md / add_missing_items_from_file.py:
ItemCode/Barcode1 and RefCode/Barcode2 are plain barcodes, not Item.item_code.
Also checks GTIN leading-zero / padding variants used by POS scanners.

Dry-run only — writes CSVs under /tmp and optionally uploads result Files to
the target site when UPLOAD_RESULTS=True (File insert only, no Item creates).

Run:
    bench --site szl console
    exec(open("apps/aimatic/ipos_data_migration/audit_s7_barcodes_vs_szl.py").read())
    main()
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import frappe
from frappe.utils import now_datetime
from openpyxl import Workbook

# Hard targets — refuse accidental runs on the wrong site/file.
TARGET_SITE = "szl"
SOURCE_XLS = "/home/nabeel/frappe-bench/sites/szl/private/files/1007.xls"
SOURCE_CSV = "/tmp/s7_xls_convert_fresh/1007-Sheet1.csv"
SOURCE_FILE_ID = "c893bbf6c2"
UPLOAD_RESULTS = True  # File docs only; never creates Items
RESULT_FOLDER = "Home/Migrations/S7"


def _assert_target():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing to run: expected site '{TARGET_SITE}', got '{site}'")
	if not Path(SOURCE_XLS).is_file() and not Path(SOURCE_CSV).is_file():
		frappe.throw(f"Source file missing: {SOURCE_XLS} (and no CSV fallback)")


def _parse_csv(path: str) -> list[dict]:
	with open(path, newline="", encoding="utf-8", errors="replace") as f:
		rows = list(csv.reader(f))
	# Header may contain an embedded newline in col1 from the Crystal .xls export.
	data = []
	for row in rows[1:]:
		if not row or not any(str(c).strip() for c in row):
			continue
		while len(row) < 7:
			row.append("")
		rec = {
			"Barcode1": str(row[0]).strip(),
			"Description": str(row[1]).strip(),
			"Onhand": str(row[2]).strip(),
			"CurCost": str(row[3]).strip(),
			"SalePrice": str(row[4]).strip(),
			"Barcode2": str(row[5]).strip(),
			"Total": str(row[6]).strip(),
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
	# Expand variant index once for O(1) lookups
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
		# canon may be the stored barcode; map via barcode_to_item
		if canon in barcode_to_item:
			return barcode_to_item[canon]
	return None


def _ensure_folder(path: str) -> str:
	"""Create nested Folder docs under Home/... ; return leaf folder name."""
	parts = [p for p in path.split("/") if p]
	if not parts or parts[0] != "Home":
		frappe.throw(f"Folder path must start with Home/: {path}")
	parent = "Home"
	for part in parts[1:]:
		name = f"{parent}/{part}" if parent != "Home" else f"Home/{part}"
		# Frappe Folder names are the full path
		folder_name = f"{parent}/{part}"
		if not frappe.db.exists("File", {"is_folder": 1, "name": folder_name}):
			# Prefer frappe create_new_folder if available
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


def _upload_file(local_path: str, folder: str, stamp: str = "") -> str:
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
	_assert_target()
	csv_path = SOURCE_CSV if Path(SOURCE_CSV).is_file() else None
	if not csv_path:
		frappe.throw(
			f"Converted CSV missing at {SOURCE_CSV}. Convert the .xls with libreoffice first."
		)

	rows = _parse_csv(csv_path)
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
	out_dir = Path(f"/tmp/s7_barcode_audit_{stamp}")
	out_dir.mkdir(parents=True, exist_ok=True)

	miss_headers = ["Barcode1", "Barcode2", "Description", "Onhand", "CurCost", "SalePrice", "Total"]
	miss_path = out_dir / f"s7_missing_items_{stamp}.xlsx"
	_write_xlsx(
		miss_path,
		miss_headers,
		[[r[h] for h in miss_headers] for r in missing_rows],
	)

	by_code = {}
	for r in rows:
		for role, code in (("Barcode1", r["Barcode1"]), ("Barcode2", r["Barcode2"])):
			if code and code in missing_barcode_set and code not in by_code:
				by_code[code] = (role, r["Description"], r["Onhand"], r["SalePrice"])
	abs_path = out_dir / f"s7_absent_barcodes_{stamp}.xlsx"
	_write_xlsx(
		abs_path,
		["barcode", "seen_as", "description_sample", "onhand", "sale_price"],
		[[code, *by_code[code]] for code in sorted(by_code)],
	)

	orphan_headers = [
		"Barcode1",
		"missing_Barcode2",
		"existing_item",
		"Description",
		"Onhand",
		"SalePrice",
	]
	orphan_path = out_dir / f"s7_orphan_barcode2_{stamp}.xlsx"
	_write_xlsx(
		orphan_path,
		orphan_headers,
		[
			[
				r["Barcode1"],
				r["missing_barcode"],
				r["existing_item"],
				r["Description"],
				r["Onhand"],
				r["SalePrice"],
			]
			for r in orphan_second
		],
	)

	summary = {
		"stamp": stamp,
		"site": TARGET_SITE,
		"source_file": "1007.xls",
		"source_file_id": SOURCE_FILE_ID,
		"source_rows": len(rows),
		"matched_rows": len(matched_rows),
		"missing_rows_neither_barcode_on_szl": len(missing_rows),
		"unique_source_barcodes": len(all_codes),
		"unique_barcodes_present_on_szl": len(present_barcode_set),
		"unique_barcodes_absent_from_szl": len(missing_barcode_set),
		"matched_rows_with_missing_barcode2_only": len(orphan_second),
		"szl_item_count": len(existing_items),
		"szl_barcode_count": len(existing_barcodes),
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

	summary_path = out_dir / f"s7_barcode_summary_{stamp}.json"
	summary_path.write_text(json.dumps(summary, indent=2))

	if UPLOAD_RESULTS:
		folder = _ensure_folder(RESULT_FOLDER)
		summary["uploaded_file_ids"]["missing_items"] = _upload_file(str(miss_path), folder, stamp)
		summary["uploaded_file_ids"]["absent_barcodes"] = _upload_file(str(abs_path), folder, stamp)
		summary["uploaded_file_ids"]["orphan_barcode2"] = _upload_file(str(orphan_path), folder, stamp)
		summary_path.write_text(json.dumps(summary, indent=2))
		summary["uploaded_file_ids"]["summary_json"] = _upload_file(str(summary_path), folder, stamp)
		summary_path.write_text(json.dumps(summary, indent=2))
		print(f"Uploaded summary File {summary['uploaded_file_ids']['summary_json']}")

	print(json.dumps(summary, indent=2))
	return summary
