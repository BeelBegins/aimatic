"""Repair missing opening Loyalty Point Entries from CustomerDataVIP.xlsx.

Idempotent: only creates Journal Entry-marked opening LPEs for Excel groups
with nonzero rounded points when that customer has no such opening entry and
no positive loyalty balance. Does not invent points for Excel-zero customers.

Does NOT import import_szl_customers.py (that module auto-runs on import).

Usage:

  bench --site szl console
  >>> exec(
  ...     open(
  ...         "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/repair_szl_loyalty_opening_points.py"
  ...     ).read()
  ... )
  >>> run(dry_run=True)
  >>> run(dry_run=False)
"""

from __future__ import annotations

import collections
import re
import xml.etree.ElementTree as ET
import zipfile

import frappe
from frappe.utils import add_days, cint, flt, today

from aimatic.offline_pos.customer_validation import normalize_pak_mobile

FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/customerdatavip.xlsx"
LOYALTY_PROGRAM_NAME = "Siezal Loyalty Program"
LOYALTY_EXPIRY_DAYS = 365
TARGET_SITE = "szl"

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
			first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
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

			values = [values_by_index.get(i, "") for i in range(max_index + 1)]
			if header is None:
				header = values
				continue
			if not any(str(v).strip() for v in values):
				continue

			record = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
			rows.append(record)
		return rows


def load_rows():
	raw = parse_first_sheet_rows(FILE_PATH)
	rows = []
	for r in raw:
		mobile_raw = str(r.get("MobileNo", "")).strip()
		normalized = normalize_pak_mobile(mobile_raw) or mobile_raw
		rows.append(
			{
				"customer_code": str(r.get("CustomerCode", "")).strip(),
				"customer_name": str(r.get("CustomerName", "")).strip(),
				"mobile": normalized,
				"loyalty_points": flt(r.get("LoyaltyPoints") or 0),
			}
		)
	return rows


def build_groups(rows):
	groups = collections.OrderedDict()
	for row in rows:
		key = row["mobile"] or f"code:{row['customer_code']}"
		groups.setdefault(key, []).append(row)
	return groups


def resolve_customer(mobile: str, members: list[dict]) -> str | None:
	if mobile and not mobile.startswith("code:"):
		name = frappe.db.get_value("Customer", {"mobile_no": mobile, "disabled": 0}, "name")
		if name:
			return name
		digits = re.sub(r"\D", "", mobile)
		if len(digits) >= 10:
			rows = frappe.db.sql(
				"""
                SELECT name FROM `tabCustomer`
                WHERE disabled = 0
                  AND REPLACE(REPLACE(REPLACE(mobile_no,' ',''),'-',''),'+','') LIKE %s
                ORDER BY modified DESC LIMIT 1
                """,
				(f"%{digits[-10:]}",),
			)
			if rows:
				return rows[0][0]

	for member in members:
		code = member.get("customer_code") or ""
		if not code:
			continue
		if frappe.db.has_column("Customer", "custom_legacy_customer_code"):
			name = frappe.db.get_value("Customer", {"custom_legacy_customer_code": code}, "name")
			if name:
				return name
		name = frappe.db.get_value("Customer", {"name": code}, "name")
		if name:
			return name
	return None


def run(dry_run: bool = True, posting_date: str | None = None):
	if frappe.local.site != TARGET_SITE:
		frappe.throw(f"Locked to site '{TARGET_SITE}', current is '{frappe.local.site}'.")

	posting_date = posting_date or today()
	expiry_date = add_days(posting_date, LOYALTY_EXPIRY_DAYS)
	company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
		"Company", {}, "name", order_by="creation asc"
	)
	loyalty_program = frappe.db.get_value(
		"Loyalty Program", {"loyalty_program_name": LOYALTY_PROGRAM_NAME}, "name"
	) or frappe.db.get_value("Loyalty Program", {"name": LOYALTY_PROGRAM_NAME}, "name")
	if not loyalty_program:
		frappe.throw(f"Loyalty Program '{LOYALTY_PROGRAM_NAME}' not found")

	groups = build_groups(load_rows())
	stats = collections.Counter()
	sample_create = []
	failures = []

	for mobile, members in groups.items():
		total_points = sum(flt(m["loyalty_points"]) for m in members)
		rounded_points = cint(round(total_points))
		if not rounded_points:
			stats["excel_zero_skipped"] += 1
			continue

		stats["excel_nonzero_groups"] += 1
		customer = resolve_customer(mobile, members)
		if not customer:
			stats["missing_customer"] += 1
			failures.append({"mobile": mobile, "points": rounded_points, "reason": "no customer"})
			continue

		if frappe.db.exists(
			"Loyalty Point Entry",
			{"customer": customer, "loyalty_program": loyalty_program, "invoice_type": "Journal Entry"},
		):
			stats["already_has_opening"] += 1
			continue

		existing = frappe.db.sql(
			"""
            SELECT COALESCE(SUM(loyalty_points), 0)
            FROM `tabLoyalty Point Entry`
            WHERE customer = %s AND loyalty_program = %s
            """,
			(customer, loyalty_program),
		)[0][0]
		if flt(existing) > 0:
			stats["already_has_positive_balance"] += 1
			continue

		stats["would_create"] += 1
		if len(sample_create) < 25:
			sample_create.append(
				{"customer": customer, "mobile": mobile, "points": rounded_points, "raw": float(total_points)}
			)

		if dry_run:
			continue

		try:
			if not frappe.db.get_value("Customer", customer, "loyalty_program"):
				frappe.db.set_value(
					"Customer", customer, "loyalty_program", loyalty_program, update_modified=False
				)

			legacy_codes = ",".join(m["customer_code"] for m in members)
			reason = f"Legacy iPOS opening balance repair (CustomerDataVIP, codes {legacy_codes})"
			entry = frappe.new_doc("Loyalty Point Entry")
			entry.loyalty_program = loyalty_program
			entry.customer = customer
			entry.loyalty_points = rounded_points
			entry.purchase_amount = 0
			entry.posting_date = posting_date
			entry.expiry_date = expiry_date
			entry.company = company
			entry.invoice_type = "Journal Entry"
			entry.discretionary_reason = reason
			entry.insert(ignore_permissions=True)
			frappe.db.commit()
			stats["created"] += 1
		except Exception as exc:
			frappe.db.rollback()
			stats["failed"] += 1
			failures.append({"customer": customer, "mobile": mobile, "reason": str(exc)})

	print(f"dry_run={dry_run} posting_date={posting_date} program={loyalty_program} company={company}")
	print("STATS", dict(stats))
	print("SAMPLE_CREATE", sample_create)
	print("FAILURES", len(failures))
	for row in failures[:30]:
		print("FAILURE", row)
	return {"stats": dict(stats), "sample": sample_create, "failures": failures}
