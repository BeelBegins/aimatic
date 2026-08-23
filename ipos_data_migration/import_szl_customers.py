import collections
import re
import xml.etree.ElementTree as ET
import zipfile

import frappe
from frappe.utils import add_days, cint, flt

from aimatic.offline_pos.customer_validation import normalize_pak_mobile

# --- Edit these before each run -------------------------------------------------
TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/customerdatavip.xlsx"
# None -> uses today(). Before a REAL migration cutover, set an explicit date.
POSTING_DATE = None
CUSTOMER_GROUP = "Individual"
TERRITORY = "Pakistan"
LOYALTY_PROGRAM_NAME = "Siezal Loyalty Program"
LOYALTY_CONVERSION_FACTOR = 1.0  # 1 Loyalty Point = Rs 1
LOYALTY_EXPIRY_DAYS = 365
# True -> parse and print counts only, no insert/submit/commit. Set False for
# the real pass, only after reviewing the dry-run summary and taking a backup.
# Keep True by default: importing/executing this file must never mutate szl.
DRY_RUN = True
# ---------------------------------------------------------------------------------

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
			record["_rownum"] = int(row.attrib.get("r", "0"))
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
				"mobile_raw": mobile_raw,
				"mobile": normalized,
				"loyalty_points": flt(r.get("LoyaltyPoints") or 0),
				"_rownum": r.get("_rownum"),
			}
		)
	return rows


def build_groups(rows):
	"""Group legacy rows by normalized MobileNo (the Customer PK -- see
	customer_validation.validate_customer, which throws on a duplicate
	normalized number). A handful of legacy CustomerCodes share one real
	person's number under a different code/name spelling; per explicit
	decision (2026-08-02) these merge into one Customer: loyalty points
	summed, every legacy CustomerCode kept (comma-joined), same shape as the
	supplier NTN-merge convention in import_siezal_suppliers.py."""
	groups = collections.OrderedDict()
	for row in rows:
		groups.setdefault(row["mobile"], []).append(row)
	return groups


def pick_winner(members):
	"""The row that decides the merged Customer's name when a group has more
	than one legacy row: highest |LoyaltyPoints| (no debit/credit activity
	signal exists for customers the way it does for suppliers, so points
	magnitude is the closest available proxy)."""
	return max(members, key=lambda r: abs(r["loyalty_points"]))


def get_company():
	companies = frappe.get_all("Company", pluck="name")
	if len(companies) != 1:
		frappe.throw(
			f"Expected exactly one Company on this site to default to, found {companies}. "
			"Edit get_company() to pick the right one before running."
		)
	return companies[0]


def ensure_customer_legacy_code_field():
	"""Custom Field, same shape/convention as Supplier.custom_legacy_supplier_code
	(apps/aimatic/aimatic/fixtures/custom_field.json). Not yet fixture-tracked --
	run `bench --site szl export-fixtures --app aimatic` after a real pass to
	capture it."""
	name = "Customer-custom_legacy_customer_code"
	if frappe.db.exists("Custom Field", name):
		return
	field = frappe.new_doc("Custom Field")
	field.dt = "Customer"
	field.fieldname = "custom_legacy_customer_code"
	field.label = "Legacy Customer Code"
	field.fieldtype = "Data"
	field.insert_after = "customer_name"
	field.read_only = 1
	field.description = "CustomerCode(s) from the legacy iPOS system (comma-joined if merged by MobileNo)."
	field.insert(ignore_permissions=True)


def ensure_loyalty_program(company):
	if frappe.db.exists("Loyalty Program", LOYALTY_PROGRAM_NAME):
		return LOYALTY_PROGRAM_NAME
	doc = frappe.new_doc("Loyalty Program")
	doc.loyalty_program_name = LOYALTY_PROGRAM_NAME
	doc.loyalty_program_type = "Single Tier Program"
	doc.from_date = POSTING_DATE or frappe.utils.today()
	doc.customer_group = CUSTOMER_GROUP
	doc.customer_territory = TERRITORY
	doc.auto_opt_in = 1
	doc.conversion_factor = LOYALTY_CONVERSION_FACTOR
	doc.expiry_duration = LOYALTY_EXPIRY_DAYS
	doc.company = company
	# Required child row for core's own point-entry creation path; aimatic's
	# own item-group-weighted hook (aimatic.loyalty.events) recalculates the
	# actual points in place after submit, so this flat factor is never
	# actually used to compute a real sale's points -- see the
	# loyalty-gift-voucher skill.
	doc.append("collection_rules", {"tier_name": "Standard", "min_spent": 0, "collection_factor": 1})
	doc.insert(ignore_permissions=True)
	return doc.name


def create_customers(groups, company, loyalty_program):
	"""Phase 1: one Customer per normalized MobileNo group.
	Returns (mobile -> customer name map, stats, failures)."""
	mobile_to_customer = {}
	stats = collections.Counter()
	failures = []

	for mobile, members in groups.items():
		winner = pick_winner(members)
		legacy_codes = ",".join(m["customer_code"] for m in members)

		try:
			existing = frappe.db.get_value("Customer", {"mobile_no": mobile})
			if not existing:
				existing = frappe.db.get_value("Customer", {"custom_legacy_customer_code": legacy_codes})

			if existing:
				customer_name = existing
				stats["customers_reused"] += 1
			else:
				customer_name_value = winner["customer_name"].title() if winner["customer_name"] else mobile
				if frappe.db.exists("Customer", customer_name_value):
					customer_name_value = f"{customer_name_value} ({mobile})"

				customer = frappe.new_doc("Customer")
				customer.customer_name = customer_name_value
				customer.customer_type = "Individual"
				customer.customer_group = CUSTOMER_GROUP
				customer.territory = TERRITORY
				customer.mobile_no = mobile
				customer.custom_legacy_customer_code = legacy_codes
				customer.loyalty_program = loyalty_program
				customer.insert(ignore_permissions=True)

				customer_name = customer.name
				stats["customers_created"] += 1

			mobile_to_customer[mobile] = customer_name

		except Exception as exc:
			frappe.db.rollback()
			stats["group_failed"] += 1
			failures.append({"mobile": mobile, "codes": legacy_codes, "reason": str(exc)})
			print(f"FAILED customer group mobile={mobile} codes={legacy_codes} -> {exc}")
			continue

		frappe.db.commit()

	return mobile_to_customer, stats, failures


def create_opening_loyalty_entries(groups, mobile_to_customer, company, loyalty_program, posting_date):
	"""Phase 2: one Loyalty Point Entry per merged Customer with a nonzero
	summed legacy balance. loyalty_points is an Int field so fractional
	legacy balances (e.g. 137.21) are rounded to the nearest whole point --
	logged per row so any rounding drift is auditable."""
	expiry_date = add_days(posting_date, LOYALTY_EXPIRY_DAYS)
	stats = collections.Counter()
	failures = []

	for mobile, members in groups.items():
		total_points = sum(m["loyalty_points"] for m in members)
		rounded_points = cint(round(total_points))
		if not rounded_points:
			continue

		customer = mobile_to_customer.get(mobile)
		if not customer:
			stats["skipped_no_customer"] += 1
			continue

		legacy_codes = ",".join(m["customer_code"] for m in members)
		reason = f"Legacy iPOS opening balance (CustomerDataVIP import, codes {legacy_codes})"

		if frappe.db.exists(
			"Loyalty Point Entry",
			{"customer": customer, "loyalty_program": loyalty_program, "invoice_type": "Journal Entry"},
		):
			stats["entries_reused"] += 1
			continue

		try:
			entry = frappe.new_doc("Loyalty Point Entry")
			entry.loyalty_program = loyalty_program
			entry.customer = customer
			entry.loyalty_points = rounded_points
			entry.purchase_amount = 0
			entry.posting_date = posting_date
			entry.expiry_date = expiry_date
			entry.company = company
			entry.invoice_type = "Journal Entry"  # placeholder marker: no real source document
			entry.discretionary_reason = reason
			entry.insert(ignore_permissions=True)
			stats["entries_created"] += 1
			if abs(total_points - rounded_points) >= 0.01:
				print(f"ROUNDED points for {customer}: {total_points} -> {rounded_points}")

		except Exception as exc:
			frappe.db.rollback()
			stats["entries_failed"] += 1
			failures.append({"customer": customer, "mobile": mobile, "reason": str(exc)})
			print(f"FAILED loyalty entry for {customer} ({mobile}) -> {exc}")
			continue

		frappe.db.commit()

	return stats, failures


def run():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(
			f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'."
		)

	posting_date = POSTING_DATE or frappe.utils.today()
	rows = load_rows()
	groups = build_groups(rows)
	merged_groups = {m: rs for m, rs in groups.items() if len(rs) > 1}
	total_points = sum(r["loyalty_points"] for r in rows)

	print(f"*** DRY_RUN = {DRY_RUN} *** Posting date for opening entries: {posting_date}")
	print(
		f"Loaded {len(rows)} legacy rows -> {len(groups)} customer groups ({len(merged_groups)} merged by mobile)"
	)
	print(f"Total legacy LoyaltyPoints across all rows: {total_points}")
	for mobile, members in list(merged_groups.items())[:20]:
		print(
			"MERGE", mobile, [(m["customer_code"], m["customer_name"], m["loyalty_points"]) for m in members]
		)

	if DRY_RUN:
		print("DRY_RUN is True -- no records were created. Set DRY_RUN = False to run for real.")
		return

	company = get_company()
	ensure_customer_legacy_code_field()
	loyalty_program = ensure_loyalty_program(company)
	print(f"Using Loyalty Program: {loyalty_program}")

	mobile_to_customer, customer_stats, customer_failures = create_customers(groups, company, loyalty_program)
	print("PHASE 1 (customers) SUMMARY", dict(customer_stats))
	print("CUSTOMER FAILURES", len(customer_failures))
	for f in customer_failures[:50]:
		print("CUSTOMER FAILURE", f)

	entry_stats, entry_failures = create_opening_loyalty_entries(
		groups, mobile_to_customer, company, loyalty_program, posting_date
	)
	print("PHASE 2 (opening loyalty points) SUMMARY", dict(entry_stats))
	print("LOYALTY ENTRY FAILURES", len(entry_failures))
	for f in entry_failures[:50]:
		print("LOYALTY ENTRY FAILURE", f)


run()
