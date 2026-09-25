"""Read-only: can the 4,897 S5-missing rows be created from Ho-MasterItemFilea16c3b.xlsx?

Adapted from reconcile_s7_missing_vs_master.py. Allows mock (`siezal`) or
live (`szl`) as target -- this is a first run against the mock, mirroring
audit_s5_barcodes_vs_szl.py's reasoning, unlike the S7 script's single hard
target. Does not create Items / groups / tax categories / brands. Writes
Excel reports under /tmp and uploads to Home/Migrations/S5.

EXISTING_GROUP_ALIASES below is carried over unchanged from the S7 script:
the master item file is the same company-wide catalog/taxonomy each time
(only the branch onhand file changes per branch), and every alias target is
an Item Group that already concretely exists on site (Household Essentials,
Colour Cosmetics, Tobacco -- the latter created during S7's approved
exception, not invented here). Reusing them is not a new create-decision;
any S5 SubCatName not covered by this table still blocks as unknown_subcat
and needs its own approval, same as S7's gate.

Run via shared ns in bench console (see other migration scripts).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import frappe
from frappe.utils import now_datetime
from openpyxl import Workbook, load_workbook

ALLOWED_SITES = ("szl",)
MASTER_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/Ho-MasterItemFilea16c3b.xlsx"
MASTER_FILE_ID = "c02b75bf48"
MISSING_XLSX = "/tmp/s5_barcode_audit_20260912_235236/s5_missing_items_20260912_235236.xlsx"
MISSING_CSV = "/tmp/s5_barcode_audit_20260912_235236/s5_missing_items_20260912_235236.csv"
RESULT_FOLDER = "Home/Migrations/S5"
UPLOAD = True

# Master SubCatName (upper) -> existing Item Group name on target site.
# Never point at a group that does not already exist. Never create groups.
# Carried over from S7 (see module docstring) -- reused, not reinvented.
EXISTING_GROUP_ALIASES = {
	"HOUSEHOLD SUNDRIES": "Household Essentials",
	"HAJI MOEEN (COSMETICS)": "Colour Cosmetics",
	"TOBACCO": "Tobacco",
	"ELECTONIC ITEMS": "Electronic Items",
	"WATCH": "Wrist Watches",
	"PET ACCESSORIES": "Animal - Pet Items",
	"FRUITES & VEGETABLES": "Fruits & Vegetables",
}


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


def _variants(code: str) -> set[str]:
	out = {code}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for n in (12, 13, 14):
			out.add(code.zfill(n))
			out.add(stripped.zfill(n))
	return out


def _title_case(s: str) -> str:
	s = " ".join(str(s or "").split())
	if not s or s.upper() == "NULL":
		return ""
	return s.title()


def _norm_brand_key(name: str) -> str:
	return "".join(ch for ch in name.upper() if ch.isalnum())



def _latest_missing_xlsx() -> str:
	matches = sorted(Path("/tmp").glob("s5_barcode_audit_*/s5_missing_items_*.xlsx"))
	if matches:
		return str(matches[-1])
	if Path(MISSING_XLSX).is_file():
		return MISSING_XLSX
	frappe.throw("Missing barcode-audit output. Run audit_s5_barcodes_vs_szl.py first.")


def load_missing_rows() -> list[dict]:
	path = Path(_latest_missing_xlsx())
	if path.is_file():
		wb = load_workbook(path, read_only=True, data_only=True)
		ws = wb.active
		rows_iter = ws.iter_rows(values_only=True)
		header = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
		out = []
		for row in rows_iter:
			rec = {header[i]: (row[i] if i < len(row) else None) for i in range(len(header))}
			out.append(
				{
					"Barcode1": _norm_code(rec.get("Barcode1")),
					"Barcode2": _norm_code(rec.get("Barcode2")),
					"Description": str(rec.get("Description") or "").strip(),
					"Onhand": rec.get("Onhand"),
					"CurCost": rec.get("CurCost"),
					"SalePrice": rec.get("SalePrice"),
				}
			)
		wb.close()
		return out
	with open(MISSING_CSV, newline="", encoding="utf-8") as f:
		return [
			{
				"Barcode1": _norm_code(r["Barcode1"]),
				"Barcode2": _norm_code(r["Barcode2"]),
				"Description": (r.get("Description") or "").strip(),
				"Onhand": r.get("Onhand"),
				"CurCost": r.get("CurCost"),
				"SalePrice": r.get("SalePrice"),
			}
			for r in csv.DictReader(f)
		]


def load_master() -> tuple[list[dict], dict[str, list[dict]]]:
	wb = load_workbook(MASTER_PATH, read_only=True, data_only=True)
	ws = wb.active
	rows_iter = ws.iter_rows(values_only=True)
	header = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
	rows = []
	index = defaultdict(list)
	for row in rows_iter:
		rec = {header[i]: (row[i] if i < len(row) else None) for i in range(len(header))}
		item = _norm_code(rec.get("ItemCode"))
		ref = _norm_code(rec.get("RefCode"))
		if not item and not ref:
			continue
		rec["_ItemCode"] = item
		rec["_RefCode"] = ref
		rows.append(rec)
		for code in (item, ref):
			if not code:
				continue
			for v in _variants(code):
				index[v].append(rec)
	wb.close()
	return rows, index


def resolve_item_group(subcat: str, group_by_lower: dict[str, str]):
	name = (subcat or "").strip()
	if not name or name.upper() == "NULL":
		return None, "blank_subcat"
	alias_target = EXISTING_GROUP_ALIASES.get(name.upper())
	if alias_target:
		hit = group_by_lower.get(alias_target.lower())
		if not hit:
			return None, f"alias_target_missing:{alias_target}"
		return hit, "ok_alias_existing"
	hit = group_by_lower.get(name.lower())
	if hit:
		return hit, "ok"
	return None, "unknown_subcat"


def resolve_fbr(cat: str, fbr_set: set[str], fbr_lower: dict[str, str]):
	name = (cat or "").strip()
	if not name or name.upper() == "NULL":
		return None, "blank_fbr"
	if name in fbr_set:
		return name, "ok"
	hit = fbr_lower.get(name.lower())
	if hit:
		return hit, "ok_casefold"
	return None, "unknown_fbr"


def resolve_brand(brand: str, brand_exact: dict[str, str], brand_norm: dict[str, list[str]]):
	name = (brand or "").strip()
	if not name or name.upper() == "NULL":
		return None, "blank_brand_ok"
	if name in brand_exact:
		return brand_exact[name], "ok"
	low = name.lower()
	for k, v in brand_exact.items():
		if k.lower() == low:
			return v, "ok_casefold"
	return None, "left_blank_no_existing_match"


def write_xlsx(path: Path, headers: list[str], rows: list[list]):
	wb = Workbook()
	ws = wb.active
	ws.title = "Sheet1"
	ws.append(headers)
	for r in rows:
		ws.append(list(r))
	wb.save(path)


def upload(path: Path, folder: str) -> str:
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": path.name,
			"is_private": 1,
			"folder": folder,
			"content": path.read_bytes(),
		}
	)
	f.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"Uploaded {f.name} {path.name}")
	return f.name


def main():
	site = _assert_site()
	missing = load_missing_rows()
	master_rows, master_index = load_master()

	groups = frappe.get_all("Item Group", fields=["name", "item_group_name"])
	group_by_lower = {}
	for g in groups:
		for key in (g.name, g.item_group_name):
			if key:
				group_by_lower.setdefault(str(key).lower(), g.name)

	fbr_names = frappe.get_all("FBR Tax Category", pluck="name")
	fbr_set = set(fbr_names)
	fbr_lower = {n.lower(): n for n in fbr_names}

	brands = frappe.get_all("Brand", pluck="name")
	brand_exact = {b: b for b in brands}
	brand_norm = defaultdict(list)
	for b in brands:
		brand_norm[_norm_brand_key(b)].append(b)

	ready = []
	blocked = []
	not_in_master = []
	multi_master = []

	for m in missing:
		codes = [c for c in (m["Barcode1"], m["Barcode2"]) if c]
		hits = []
		seen_ids = set()
		for c in codes:
			for v in _variants(c):
				for rec in master_index.get(v, []):
					rid = id(rec)
					if rid not in seen_ids:
						seen_ids.add(rid)
						hits.append(rec)
		if not hits:
			not_in_master.append(m)
			blocked.append({**m, "block_reason": "not_in_master", "master_itemcode": "", "master_refcode": ""})
			continue
		if len(hits) > 1:
			exact = [h for h in hits if h["_ItemCode"] in codes or h["_RefCode"] in codes]
			hits = exact or hits
		if len(hits) > 1:
			multi_master.append({**m, "master_matches": len(hits)})
			blocked.append(
				{
					**m,
					"block_reason": f"ambiguous_master:{len(hits)}",
					"master_itemcode": hits[0]["_ItemCode"],
					"master_refcode": hits[0]["_RefCode"],
				}
			)
			continue

		rec = hits[0]
		subcat = str(rec.get("SubCatName") or "").strip()
		fbr_raw = str(rec.get("Fbr_Tax_Category") or "").strip()
		brand_raw = str(rec.get("BrandName") or "").strip()
		desc_raw = str(rec.get("Description") or "").strip()

		group, group_st = resolve_item_group(subcat, group_by_lower)
		fbr, fbr_st = resolve_fbr(fbr_raw, fbr_set, fbr_lower)
		brand, brand_st = resolve_brand(brand_raw, brand_exact, brand_norm)

		reasons = []
		if group_st not in ("ok", "ok_alias_existing"):
			reasons.append(group_st if group_st != "ok" else "")
			if group_st == "unknown_subcat":
				reasons[-1] = f"unknown_subcat:{subcat}"
			elif group_st.startswith("alias_target_missing"):
				reasons[-1] = group_st
			elif group_st == "blank_subcat":
				reasons[-1] = "blank_subcat"
		if fbr_st not in ("ok", "ok_casefold"):
			reasons.append(fbr_st)
			if fbr_st == "unknown_fbr":
				reasons[-1] = f"unknown_fbr:{fbr_raw}"

		row_out = {
			"S5_Barcode1": m["Barcode1"],
			"S5_Barcode2": m["Barcode2"],
			"S5_Description": m["Description"],
			"S5_Onhand": m["Onhand"],
			"S5_CurCost": m["CurCost"],
			"S5_SalePrice": m["SalePrice"],
			"Master_ItemCode": rec["_ItemCode"],
			"Master_RefCode": rec["_RefCode"],
			"Master_Description": desc_raw,
			"item_name_proper": _title_case(desc_raw),
			"CatName": rec.get("CatName"),
			"SubCatName": subcat,
			"resolved_item_group": group or "",
			"BrandName": brand_raw,
			"brand_proper": _title_case(brand) if brand else "",
			"resolved_brand": brand or "",
			"brand_status": brand_st,
			"Fbr_Tax_Category": fbr_raw,
			"resolved_fbr": fbr or "",
			"fbr_status": fbr_st,
			"MRP": rec.get("MRP"),
			"Master_Slprice": rec.get("Slprice"),
		}

		if reasons:
			blocked.append({**m, **row_out, "block_reason": "|".join(reasons)})
		else:
			ready.append(row_out)

	unknown_subcats = sorted(
		{
			str(r.get("SubCatName") or "").strip()
			for r in blocked
			if str(r.get("block_reason", "")).find("unknown_subcat") >= 0
		}
		- {""}
	)
	unknown_fbrs = sorted(
		{
			str(r.get("Fbr_Tax_Category") or "").strip()
			for r in blocked
			if str(r.get("block_reason", "")).find("unknown_fbr") >= 0
			or str(r.get("block_reason", "")).find("blank_fbr") >= 0
		}
	)
	new_brands = sorted(
		{
			str(r.get("BrandName") or "").strip()
			for r in ready
			if r.get("brand_status") == "left_blank_no_existing_match"
		}
		- {""}
	)
	brand_left_blank_rows = sum(1 for r in ready if r.get("brand_status") == "left_blank_no_existing_match")

	stamp = now_datetime().strftime("%Y%m%d_%H%M%S")
	out_dir = Path(f"/tmp/s5_master_reconcile_{stamp}")
	out_dir.mkdir(parents=True, exist_ok=True)

	ready_headers = [
		"S5_Barcode1",
		"S5_Barcode2",
		"S5_Description",
		"S5_Onhand",
		"S5_CurCost",
		"S5_SalePrice",
		"Master_ItemCode",
		"Master_RefCode",
		"item_name_proper",
		"resolved_item_group",
		"SubCatName",
		"resolved_brand",
		"brand_proper",
		"resolved_fbr",
		"MRP",
		"Master_Slprice",
		"brand_status",
		"fbr_status",
	]
	ready_path = out_dir / f"s5_create_ready_{stamp}.xlsx"
	write_xlsx(ready_path, ready_headers, [[r.get(h, "") for h in ready_headers] for r in ready])

	blocked_headers = [
		"block_reason",
		"S5_Barcode1",
		"S5_Barcode2",
		"S5_Description",
		"S5_Onhand",
		"Master_ItemCode",
		"Master_RefCode",
		"Master_Description",
		"SubCatName",
		"resolved_item_group",
		"BrandName",
		"Fbr_Tax_Category",
		"resolved_fbr",
	]
	blocked_norm = []
	for r in blocked:
		blocked_norm.append(
			{
				"block_reason": r.get("block_reason", ""),
				"S5_Barcode1": r.get("S5_Barcode1") or r.get("Barcode1", ""),
				"S5_Barcode2": r.get("S5_Barcode2") or r.get("Barcode2", ""),
				"S5_Description": r.get("S5_Description") or r.get("Description", ""),
				"S5_Onhand": r.get("S5_Onhand") or r.get("Onhand", ""),
				"Master_ItemCode": r.get("Master_ItemCode") or r.get("master_itemcode", ""),
				"Master_RefCode": r.get("Master_RefCode") or r.get("master_refcode", ""),
				"Master_Description": r.get("Master_Description", ""),
				"SubCatName": r.get("SubCatName", ""),
				"resolved_item_group": r.get("resolved_item_group", ""),
				"BrandName": r.get("BrandName", ""),
				"Fbr_Tax_Category": r.get("Fbr_Tax_Category", ""),
				"resolved_fbr": r.get("resolved_fbr", ""),
			}
		)
	blocked_path = out_dir / f"s5_create_blocked_{stamp}.xlsx"
	write_xlsx(blocked_path, blocked_headers, [[r.get(h, "") for h in blocked_headers] for r in blocked_norm])

	gates_path = out_dir / f"s5_approval_gates_{stamp}.xlsx"
	wb = Workbook()
	ws = wb.active
	ws.title = "Unknown SubCats"
	ws.append(["SubCatName", "needs_approval_to_create_item_group"])
	for s in unknown_subcats:
		ws.append([s, "YES"])
	ws2 = wb.create_sheet("Unknown FBR")
	ws2.append(["Fbr_Tax_Category", "needs_approval"])
	for s in unknown_fbrs:
		ws2.append([s, "YES"])
	ws3 = wb.create_sheet("Brands Left Blank")
	ws3.append(["BrandName", "action"])
	for s in new_brands:
		ws3.append([s, "leave Item.brand empty — no new Brand"])
	wb.save(gates_path)

	summary = {
		"stamp": stamp,
		"site": site,
		"master_file": "Ho-MasterItemFilea16c3b.xlsx",
		"master_file_id": MASTER_FILE_ID,
		"master_rows": len(master_rows),
		"s5_missing_rows": len(missing),
		"create_ready": len(ready),
		"blocked": len(blocked),
		"not_in_master": len(not_in_master),
		"ambiguous_master": len(multi_master),
		"unknown_subcat_count": len(unknown_subcats),
		"unknown_or_blank_fbr_values": unknown_fbrs,
		"brand_left_blank_rows": brand_left_blank_rows,
		"brand_left_blank_names": new_brands,
		"unknown_subcats": unknown_subcats,
		"verdict": (
			"ALL_READY" if len(ready) == len(missing) else "PARTIAL_READY_NEEDS_APPROVAL_OR_MASTER_FIX"
		),
		"files": {
			"ready": str(ready_path),
			"blocked": str(blocked_path),
			"gates": str(gates_path),
		},
		"uploaded": {},
	}

	summary_path = out_dir / f"s5_master_reconcile_summary_{stamp}.json"
	summary_path.write_text(json.dumps(summary, indent=2))

	if UPLOAD:
		summary["uploaded"]["ready"] = upload(ready_path, RESULT_FOLDER)
		summary["uploaded"]["blocked"] = upload(blocked_path, RESULT_FOLDER)
		summary["uploaded"]["gates"] = upload(gates_path, RESULT_FOLDER)
		summary_path.write_text(json.dumps(summary, indent=2))
		summary["uploaded"]["summary"] = upload(summary_path, RESULT_FOLDER)

	print(json.dumps(summary, indent=2))
	return summary
