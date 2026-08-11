"""Seed Supplier.custom_principals from legacy multi-NTN vendor rows on szl.

Dry-run by default. Live write from ``bench --site szl console``::

    exec(open("/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/seed_szl_supplier_principals.py").read(), globals())
    main(dry_run=False)

Principal label = text inside parentheses on each legacy SupplierName.
Only multi-row StandardNTN groups are seeded. Sister-store SIEZAL* names
are skipped. Match Supplier by tax_id == StandardNTN.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict

import frappe

TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/vendordataghouritown.xlsx"
DRY_RUN = True
NTN_PATTERN = re.compile(r"^[A-Za-z0-9]{7}$")
PAREN_PATTERN = re.compile(r"\(([^)]+)\)\s*$")
SISTER_PREFIX = "SIEZAL"

NS = {
	"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
	"pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_to_idx(col):
	value = 0
	for ch in col:
		value = value * 26 + (ord(ch) - 64)
	return value - 1


def parse_first_sheet_rows(path):
	with zipfile.ZipFile(path) as zf:
		shared_strings = []
		if "xl/sharedStrings.xml" in zf.namelist():
			root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
			for si in root.findall("a:si", NS):
				shared_strings.append("".join(t.text or "" for t in si.iterfind(".//a:t", NS)))

		workbook = ET.fromstring(zf.read("xl/workbook.xml"))
		rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
		rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("pr:Relationship", NS)}

		first_sheet = workbook.find("a:sheets/a:sheet", NS)
		target = rel_map[
			first_sheet.attrib[
				"{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
			]
		]
		if not target.startswith("xl/"):
			target = f"xl/{target}"

		worksheet = ET.fromstring(zf.read(target))
		header = None
		rows = []
		for row in worksheet.findall("a:sheetData/a:row", NS):
			values_by_index = {}
			max_index = -1
			for cell in row.findall("a:c", NS):
				ref = cell.attrib["r"]
				col = re.match(r"[A-Z]+", ref).group(0)
				idx = col_to_idx(col)
				max_index = max(max_index, idx)
				cell_type = cell.attrib.get("t")
				value_node = cell.find("a:v", NS)
				if value_node is None:
					value = ""
				elif cell_type == "s":
					value = shared_strings[int(value_node.text)]
				else:
					value = value_node.text or ""
				values_by_index[idx] = value
			if max_index < 0:
				continue
			values = [values_by_index.get(i, "") for i in range(max_index + 1)]
			if header is None:
				header = [str(v).strip() for v in values]
				continue
			row_dict = {
				header[i]: (values[i] if i < len(values) else "")
				for i in range(len(header))
			}
			rows.append(row_dict)
		return rows


def extract_principal_label(supplier_name: str) -> str | None:
	match = PAREN_PATTERN.search((supplier_name or "").strip())
	if not match:
		return None
	label = re.sub(r"\s+", " ", match.group(1)).strip()
	return label or None


def is_sister_store(supplier_name: str) -> bool:
	return (supplier_name or "").strip().upper().startswith(SISTER_PREFIX)


def build_ntn_groups(rows):
	groups = defaultdict(list)
	for row in rows:
		code = str(row.get("SupplierCode") or "").strip()
		name = str(row.get("SupplierName") or "").strip()
		if not code and not name:
			continue
		# Skip workbook summary/total rows with blank code.
		if not code:
			continue
		ntn = str(row.get("StandardNTN") or "").strip()
		if not NTN_PATTERN.match(ntn):
			continue
		if is_sister_store(name):
			continue
		principal = extract_principal_label(name)
		if not principal:
			continue
		groups[ntn].append(
			{
				"code": code,
				"name": name,
				"principal": principal,
			}
		)
	return {ntn: members for ntn, members in groups.items() if len(members) > 1}


def ensure_principal(name: str, stats: dict, dry_run: bool):
	if frappe.db.exists("Principal", name):
		stats["principals_existing"] += 1
		return name
	stats["principals_created"] += 1
	if dry_run:
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Principal",
			"principal_name": name,
			"disabled": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def attach_principals(supplier_name: str, members: list[dict], stats: dict, dry_run: bool):
	existing = {
		(r.principal, (r.legacy_supplier_code or "").strip())
		for r in frappe.get_all(
			"Supplier Principal",
			filters={"parent": supplier_name, "parenttype": "Supplier"},
			fields=["principal", "legacy_supplier_code"],
		)
	}
	# Also treat same principal (any code) as present so we don't duplicate
	# principal rows when re-running with different code order.
	existing_principals = {p for p, _ in existing}

	to_add = []
	for member in members:
		principal = member["principal"]
		code = member["code"]
		ensure_principal(principal, stats, dry_run)
		if principal in existing_principals:
			stats["links_existing"] += 1
			continue
		key = (principal, code)
		if key in existing:
			stats["links_existing"] += 1
			continue
		to_add.append(member)
		existing_principals.add(principal)

	if not to_add:
		return

	stats["links_created"] += len(to_add)
	stats["suppliers_updated"] += 1
	if dry_run:
		return

	doc = frappe.get_doc("Supplier", supplier_name)
	for member in to_add:
		doc.append(
			"custom_principals",
			{
				"principal": member["principal"],
				"legacy_supplier_code": member["code"],
			},
		)
	doc.save(ignore_permissions=True)


def main(dry_run=None):
	if frappe.local.site != TARGET_SITE:
		frappe.throw(f"Refusing to run on site {frappe.local.site}; expected {TARGET_SITE}")

	run_dry = DRY_RUN if dry_run is None else bool(dry_run)

	rows = parse_first_sheet_rows(FILE_PATH)
	groups = build_ntn_groups(rows)
	stats = {
		"multi_ntn_groups": len(groups),
		"suppliers_matched": 0,
		"suppliers_missing": 0,
		"suppliers_updated": 0,
		"principals_created": 0,
		"principals_existing": 0,
		"links_created": 0,
		"links_existing": 0,
	}

	for ntn, members in sorted(groups.items()):
		supplier = frappe.db.get_value("Supplier", {"tax_id": ntn}, "name")
		if not supplier:
			stats["suppliers_missing"] += 1
			print(f"MISSING supplier for NTN {ntn} ({len(members)} principals)")
			continue
		stats["suppliers_matched"] += 1
		attach_principals(supplier, members, stats, run_dry)

	if not run_dry:
		frappe.db.commit()

	mode = "DRY-RUN" if run_dry else "LIVE"
	print(f"[{mode}] seed_szl_supplier_principals complete")
	for key, value in stats.items():
		print(f"  {key}: {value}")


if __name__ == "__main__":
	main()
