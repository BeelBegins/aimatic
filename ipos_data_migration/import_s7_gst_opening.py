"""S7 follow-up to import_s7_prices_and_stock.py: post the GST portion that
was backed out of the S7 legacy CurCost before using it as the Stock Entry
valuation rate (same tax-exclusive-valuation-rate handling as import.md /
import_szl_s1_gst_opening.py). That GST was never recorded anywhere by the
main S7 stock import -- carried forward here as an opening balance on the
existing 'GST - SSM' account (Liability, Duties and Taxes) rather than
discarded, via one Journal Entry against the same Temporary Opening suspense
account the S7 stock and vendor opening balances already use.

Mirrors import_szl_s1_gst_opening.py exactly, adapted for S7's source file
and branch. Source: 1007.xls (same file import_s7_prices_and_stock.py used),
via the same positional CSV parse (Barcode1, Description, Onhand, CurCost,
SalePrice, Barcode2, Total).

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s7_gst_opening.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)  # post on szl
"""

from __future__ import annotations

import csv
from pathlib import Path

import frappe

TARGET_SITE = "szl"
S7_CSV = "/tmp/s7_xls_convert_fresh/1007-Sheet1.csv"
WAREHOUSE = "S7 - Empire Heights - SSM"
POSTING_DATE = "2026-09-06"
GST_ACCOUNT = "GST - SSM"
TAG = "S7-ONHAND-GST-OPENING-2026-09-06"
JUNK_DESCRIPTIONS = {"TEST", "TEST3"}


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")


def _norm_code(v) -> str:
	if v is None:
		return ""
	s = str(v).strip()
	if s.endswith(".0") and s.replace(".", "", 1).isdigit():
		s = s[:-2]
	return s


def _variants(code: str) -> set[str]:
	out = {code, code.upper(), code.lower()}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for n in (12, 13, 14):
			out.add(code.zfill(n))
			out.add(stripped.zfill(n))
	return {c for c in out if c}


def _as_float(v) -> float:
	try:
		return float(str(v or "").strip() or 0)
	except ValueError:
		return 0.0


def exclusive_rate(inclusive_rate, tax_rate):
	if inclusive_rate <= 0 or tax_rate <= 0:
		return inclusive_rate
	sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
	return round(inclusive_rate - sales_tax, 2)


def parse_s7_rows():
	path = Path(S7_CSV)
	if not path.is_file():
		frappe.throw(f"S7 CSV missing: {S7_CSV}")
	rows = []
	with path.open(newline="", encoding="utf-8", errors="replace") as f:
		reader = csv.reader(f)
		next(reader, None)
		for row in reader:
			if not row or not any(str(c).strip() for c in row):
				continue
			while len(row) < 7:
				row.append("")
			b1, b2 = _norm_code(row[0]), _norm_code(row[5])
			if not b1 and not b2:
				continue
			rows.append(
				{
					"Barcode1": b1,
					"Barcode2": b2,
					"Description": str(row[1] or "").strip(),
					"Onhand": _as_float(row[2]),
					"CurCost": _as_float(row[3]),
				}
			)
	return rows


def barcode_index():
	idx = {}
	for barcode, parent in frappe.db.sql(
		"SELECT barcode, parent FROM `tabItem Barcode` WHERE barcode IS NOT NULL AND barcode != ''"
	):
		code = str(barcode).strip()
		if not code:
			continue
		for v in _variants(code):
			idx.setdefault(v, parent)
	return idx


def resolve_item(row, idx):
	for code in (row["Barcode1"], row["Barcode2"]):
		if not code:
			continue
		for v in _variants(code):
			if v in idx:
				return idx[v]
	return None


def get_item_tax_rate(item_code, cache):
	if item_code not in cache:
		category = frappe.db.get_value("Item", item_code, "custom_fbr_tax_category")
		cache[item_code] = (
			_as_float(frappe.db.get_value("FBR Tax Category", category, "tax_rate")) if category else 0.0
		)
	return cache[item_code]


def get_branch_and_cost_center():
	branch = frappe.get_cached_value("Warehouse", WAREHOUSE, "custom_branch")
	if not branch:
		frappe.throw(f"Warehouse {WAREHOUSE} has no Branch mapped.")
	cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
	if not cost_center:
		frappe.throw(f"Branch {branch} has no Cost Center.")
	return branch, cost_center


def get_temp_opening_account():
	company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
	account = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
	)
	if not account:
		frappe.throw(f"No 'Temporary Opening' account found under {company}.")
	return account, company


def main(apply: bool = False):
	_assert_site()
	if frappe.db.exists("Journal Entry", {"cheque_no": TAG, "docstatus": ["!=", 2]}):
		print("Already run -- Journal Entry with this tag already exists. Nothing to do.")
		return

	rows = parse_s7_rows()
	idx = barcode_index()
	tax_rate_cache = {}

	# Merge duplicate barcode rows onto one item first (same item can appear
	# more than once in the source, exactly like import_s7_prices_and_stock.py
	# handles it) so its GST isn't double counted.
	by_item = {}
	unresolved = 0
	for row in rows:
		if row["Description"].strip().upper() in JUNK_DESCRIPTIONS:
			continue
		item_code = resolve_item(row, idx)
		if not item_code:
			unresolved += 1
			continue
		rec = by_item.setdefault(item_code, {"onhand": 0.0, "inclusive_cost": 0.0})
		rec["onhand"] += row["Onhand"]
		if rec["inclusive_cost"] <= 0 and row["CurCost"] > 0:
			rec["inclusive_cost"] = row["CurCost"]

	total_gst = 0.0
	matched_rows = 0
	for item_code, rec in by_item.items():
		tax_rate = get_item_tax_rate(item_code, tax_rate_cache)
		excl_rate = exclusive_rate(rec["inclusive_cost"], tax_rate)
		total_gst += rec["onhand"] * (rec["inclusive_cost"] - excl_rate)
		matched_rows += 1

	total_gst = round(total_gst, 2)
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"matched_items": matched_rows,
			"unresolved_rows": unresolved,
			"total_gst": total_gst,
		}
	)

	if not apply:
		print("DRY_RUN -- no Journal Entry created.")
		return {"total_gst": total_gst, "matched_items": matched_rows}

	if not total_gst:
		print("Total GST rounds to 0 -- nothing to post.")
		return {"total_gst": 0}

	branch, cost_center = get_branch_and_cost_center()
	temp_opening_account, company = get_temp_opening_account()

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.company = company
	je.posting_date = POSTING_DATE
	je.is_opening = "Yes"
	je.cheque_no = TAG
	je.cheque_date = POSTING_DATE
	je.user_remark = (
		"S7 opening stock: GST portion backed out of legacy CurCost before stock valuation (see import.md)"
	)

	amount = abs(total_gst)
	if total_gst > 0:
		je.append("accounts", {"account": GST_ACCOUNT, "debit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
		je.append("accounts", {"account": temp_opening_account, "credit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
	else:
		je.append("accounts", {"account": GST_ACCOUNT, "credit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
		je.append("accounts", {"account": temp_opening_account, "debit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})

	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	print(f"Created and submitted {je.name} for {total_gst}")
	return {"total_gst": total_gst, "je": je.name}
