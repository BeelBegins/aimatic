"""S7 Empire Heights vendor opening balances -> live szl.

Source: sites/szl/private/files/1007vendorbalances.xlsx ("1007" = S7's own
branch_code), same column shape as supplierimport.md (SupplierCode,
SupplierName, FBRTYPE, ContactPerson, NTNo, StandardNTN, WhtTax%, LedgerCode,
TotalDebit, TotalCredit, ClosingBalance).

Unlike S1's import_szl_suppliers.py, this file's SupplierCode is NOT globally
unique across branches, so automatic NTN/code matching (that script's
approach) was abandoned in favor of an explicit, manually verified per-row
resolution table. Confirmed live on szl 2026-09-06:

- Code 614 in this file ("SIEZAL SUPERMARKET (KHANNA PULL)") collides with an
  unrelated existing Supplier that S1's own import already tagged with legacy
  code "614" ("SIEZAL SUPERMARKET (BAHRIA PH7)") -- reusing it by code alone
  would have silently posted KHANNA PULL's balance onto BAHRIA PH7. No
  Supplier named/like "KHANNA PULL" exists anywhere on szl -- genuinely new.
- Code 1004 in this file ("SIEZAL SUPERMARKET BAHRIA PH7") is the *same* real
  entity as that S1-tagged "BAHRIA PH7" Supplier, just referenced under a
  different local code in S7's own ledger -- matched by name, code appended.
- A few NTNs don't match verbatim between file and stored Supplier (723
  "230823" vs stored "4230823"; 726 "389674-" vs stored "G389674") -- same
  real vendor confirmed by name, matched explicitly rather than by string eq.
- Code 081 "TEST SUPPLIER" (Rs 10 junk balance) is excluded per explicit
  instruction.

Every other code below has a clean 1:1 match (same NTN and/or literal name)
to an already-imported Supplier -- see EXISTING_SUPPLIER_MAP.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s7_vendor_balances.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import collections
import re
import xml.etree.ElementTree as ET
import zipfile

import frappe

TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/1007vendorbalances.xlsx"
COMPANY = "Siezal Supermarket"
BRANCH = "S7 - Empire Heights"
POSTING_DATE = "2026-09-06"
# Distinct from S1's "LEGACY-OB-<code>" so this script's idempotency check
# never collides with S1's opening entries, even where the same numeric
# SupplierCode is reused across the two branches' files (614, see above).
REFERENCE_PREFIX = "LEGACY-OB-S7-"

EXCLUDE_CODES = {"081"}  # TEST SUPPLIER, Rs 10 junk row -- skip per explicit instruction (2026-09-06)

# Verified 2026-09-06 by NTN and/or literal name against live szl Suppliers.
EXISTING_SUPPLIER_MAP = {
	"065": "AJMI DISTRIBUTION NETWORK PVT LTD (LAYS)",
	# Not "(SEASONS)" -- same NTN 2190273, but SEASONS was disabled 2026-08-24
	# and all purchase activity since then has continued on NESTLE YOGURT
	# (9 POs / 11 PIs through 2026-09-04 vs SEASONS's last activity 2026-08-07).
	"095": "SHAN MARKETING SERVICES (NESTLE YOGURT)",
	"1004": "SIEZAL SUPERMARKET (BAHRIA PH7)",
	"1005": "SIEZAL SUPERMARKET (BAHRIA TOWN PHASE 8)",
	"154": "PAKISTAN FRUIT JUICE COMPANY PVT LTD (HICO)",
	"177": "K&NS FOODS PVT LIMITED",
	"179": "DAWN FROZEN FOODS COMPANY PVT LTD (HFF)",
	"180": "SHAFAY FOOD PRODUCTS",
	"190": "SABIRS POULTRY PVT LTD (SABROSO)",
	"201": "CAPITAL FOODS PVT LTD",
	"205": "SADIQ FEEDS PVT LTD (SB EGGS)",
	"206": "ATA BAKERY (BREAD & BEYOND)",
	"209": "AT-TAHUR LTD (PREMA)",
	"251": "HAIDRI BEVERAGE PVT LTD (AQUAFINA)",
	"350": "CITI SERVICES DISTRIBUTORS (BLUE BAND)",
	"454": "SIEZAL SUPERMARKET (MISRIAL ROAD)",
	"556": "AI ENTERPRISES (PAK EGGS)",
	"562": "HOPE CHEMICALS PRIVATE LIMITED (CLEAN FREE)",
	"661": "SHERRY ASSOCIATES (WALS)",
	"715": "BLISS FOOD COMPANY (SPONGE)",
	"716": "BLISS FOOD COMPANY (SPONGE)",
	"723": "ARCTIC ASSOCIATES (WALLS)",
	"726": "M/S SCOURER BRITE",
}

# code -> existing Supplier that should gain this code in its comma-joined
# custom_legacy_supplier_code (same real entity, different branch-local code).
APPEND_LEGACY_CODE = {
	"1004": "SIEZAL SUPERMARKET (BAHRIA PH7)",
	"095": "SHAN MARKETING SERVICES (NESTLE YOGURT)",
}

# code -> brand-new Supplier to create. Field values mirror the convention
# already used for the other no-NTN SIEZAL SUPERMARKET (...) sister-store
# Suppliers (Distributor / Company / Non-Filers / Exempt).
NEW_SUPPLIERS = {
	"614": {
		"supplier_name": "SIEZAL SUPERMARKET (KHANNA PULL)",
		"supplier_group": "Distributor",
		"supplier_type": "Company",
		"tax_withholding_group": "Non-Filers",
		"tax_withholding_category": "Exempt",
	},
}

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


def as_float(value):
	text = str(value or "").strip()
	if not text:
		return 0.0
	return float(text)


def load_rows():
	raw = parse_first_sheet_rows(FILE_PATH)
	rows = []
	for r in raw:
		code = str(r.get("SupplierCode", "")).strip()
		name = str(r.get("SupplierName", "")).strip()
		if not code and not name:
			continue
		rows.append(
			{
				"code": code,
				"name": name,
				"fbrtype": str(r.get("FBRTYPE", "")).strip(),
				"standard_ntn": str(r.get("StandardNTN", "")).strip(),
				"ledger_code": str(r.get("LedgerCode", "")).strip(),
				"total_debit": as_float(r.get("TotalDebit")),
				"total_credit": as_float(r.get("TotalCredit")),
				"closing_balance": as_float(r.get("ClosingBalance")),
			}
		)
	return rows


def _assert_target():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")


def resolve_branch():
	branch_company = frappe.db.get_value("Branch", BRANCH, "company")
	if branch_company != COMPANY:
		frappe.throw(f"Branch {BRANCH} is not configured for {COMPANY}.")
	cost_center = frappe.get_cached_value("Branch", BRANCH, "cost_center")
	if not cost_center:
		frappe.throw(f"Branch {BRANCH} has no Cost Center configured.")
	return BRANCH, cost_center


def main(apply: bool = False):
	_assert_target()

	rows = [r for r in load_rows() if r["code"] not in EXCLUDE_CODES]
	unaccounted = [
		r["code"] for r in rows if r["code"] not in EXISTING_SUPPLIER_MAP and r["code"] not in NEW_SUPPLIERS
	]
	if unaccounted:
		frappe.throw(
			f"Source file has codes not covered by EXISTING_SUPPLIER_MAP/NEW_SUPPLIERS: {unaccounted}. "
			"Resolve manually before running -- do not guess."
		)

	branch, cost_center = resolve_branch()
	payable_account = frappe.get_cached_value("Company", COMPANY, "default_payable_account")
	temp_opening_account = frappe.db.get_value(
		"Account", {"company": COMPANY, "account_name": "Temporary Opening", "is_group": 0}
	)
	if not temp_opening_account:
		frappe.throw(f"No 'Temporary Opening' account found under {COMPANY}.")

	plan = {
		"posting_date": POSTING_DATE,
		"branch": branch,
		"cost_center": cost_center,
		"payable_account": payable_account,
		"temp_opening_account": temp_opening_account,
		"rows_total": len(rows),
		"new_suppliers": [c for c in NEW_SUPPLIERS if c in {r["code"] for r in rows}],
		"legacy_code_appends": {c: s for c, s in APPEND_LEGACY_CODE.items() if c in {r["code"] for r in rows}},
		"nonzero_balance_rows": sum(1 for r in rows if frappe.utils.flt(r["closing_balance"], 2)),
		"expected_net_closing_balance": round(sum(r["closing_balance"] for r in rows), 2),
	}
	print("PLAN", plan)

	if not apply:
		print("DRY_RUN -- no records created. Re-run with apply=True to execute.")
		return plan

	stats = collections.Counter()

	# Phase 1: create genuinely new Suppliers.
	for code, spec in NEW_SUPPLIERS.items():
		if code not in {r["code"] for r in rows}:
			continue
		if frappe.db.exists("Supplier", spec["supplier_name"]):
			stats["suppliers_reused"] += 1
			continue
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = spec["supplier_name"]
		supplier.supplier_group = spec["supplier_group"]
		supplier.supplier_type = spec["supplier_type"]
		supplier.tax_withholding_group = spec["tax_withholding_group"]
		supplier.tax_withholding_category = spec["tax_withholding_category"]
		supplier.custom_legacy_supplier_code = code
		supplier.insert(ignore_permissions=True)
		frappe.db.commit()
		stats["suppliers_created"] += 1
		print(f"Created Supplier {supplier.name} (code {code})")

	# Phase 1b: append this file's code onto an existing Supplier's legacy-code list.
	for code, supplier_name in APPEND_LEGACY_CODE.items():
		if code not in {r["code"] for r in rows}:
			continue
		existing = frappe.db.get_value("Supplier", supplier_name, "custom_legacy_supplier_code") or ""
		codes = [c.strip() for c in existing.split(",") if c.strip()]
		if code not in codes:
			codes.append(code)
			frappe.db.set_value("Supplier", supplier_name, "custom_legacy_supplier_code", ",".join(codes))
			frappe.db.commit()
			stats["legacy_codes_appended"] += 1
			print(f"Appended code {code} onto {supplier_name} -> {','.join(codes)}")
		else:
			stats["legacy_codes_already_present"] += 1

	# Phase 2: one Opening Entry Journal Entry per nonzero-balance row.
	failures = []
	for row in rows:
		balance = frappe.utils.flt(row["closing_balance"], 2)
		if not balance:
			stats["skipped_zero_balance"] += 1
			continue

		supplier = EXISTING_SUPPLIER_MAP.get(row["code"]) or NEW_SUPPLIERS.get(row["code"], {}).get("supplier_name")
		if not supplier:
			stats["skipped_no_supplier"] += 1
			continue

		reference = f"{REFERENCE_PREFIX}{row['code']}"
		if frappe.db.exists("Journal Entry", {"cheque_no": reference, "docstatus": ["!=", 2]}):
			stats["entries_reused"] += 1
			continue

		remark = (
			f"Legacy S7 vendor {row['name']} (Code {row['code']}, Ledger {row['ledger_code']}, "
			f"NTN {row['standard_ntn'] or 'n/a'})"
		)
		amount = abs(balance)

		try:
			je = frappe.new_doc("Journal Entry")
			je.voucher_type = "Opening Entry"
			je.company = COMPANY
			je.posting_date = POSTING_DATE
			je.is_opening = "Yes"
			je.cheque_no = reference
			je.cheque_date = POSTING_DATE
			je.user_remark = remark

			if balance > 0:
				je.append(
					"accounts",
					{
						"account": payable_account,
						"party_type": "Supplier",
						"party": supplier,
						"credit_in_account_currency": amount,
						"user_remark": remark,
						"branch": branch,
						"cost_center": cost_center,
					},
				)
				je.append(
					"accounts",
					{
						"account": temp_opening_account,
						"debit_in_account_currency": amount,
						"branch": branch,
						"cost_center": cost_center,
					},
				)
			else:
				je.append(
					"accounts",
					{
						"account": payable_account,
						"party_type": "Supplier",
						"party": supplier,
						"debit_in_account_currency": amount,
						"user_remark": remark,
						"branch": branch,
						"cost_center": cost_center,
					},
				)
				je.append(
					"accounts",
					{
						"account": temp_opening_account,
						"credit_in_account_currency": amount,
						"branch": branch,
						"cost_center": cost_center,
					},
				)

			je.insert(ignore_permissions=True)
			je.submit()
			frappe.db.commit()
			stats["entries_created"] += 1
		except Exception as exc:
			frappe.db.rollback()
			stats["entries_failed"] += 1
			failures.append({"code": row["code"], "name": row["name"], "reason": str(exc)})
			print(f"FAILED opening entry for {row['code']} ({row['name']}) -> {exc}")

	print("SUMMARY", dict(stats))
	print("FAILURES", failures)
	return dict(stats)
