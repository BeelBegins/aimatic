"""Back-fill the missing GST opening Journal Entries for S4, S5 and S6.

S1 and S7 got a GST opening Journal Entry (Dr ``GST - SSM`` / Cr ``Temporary
Opening``) for the GST portion backed out of the tax-INCLUSIVE CurCost before
stock valuation (import.md "Tax-exclusive valuation rate"). S4, S5 and S6 stock
was posted without that entry, so their GST was never recorded anywhere
(found 2026-10-11 while building the S2 import; S2 itself already has it).

GST per branch = plan inclusive value - plan exclusive value, from the branch's
own import_<s>_prices_and_stock.py build_plan (the very rows that were posted,
same method as import_s2_gst_opening.py). Source file hashes are verified by
those scripts, so a changed file stops the run.

Posting date = the date that branch's opening stock was posted, so the entry
lands on the same day as the stock it offsets. One Opening Entry per branch,
idempotent by tag in cheque_no, branch/cost center stamped.

Dry-run by default. Apply needs a verified backup and explicit approval per run.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s4_s5_s6_gst_opening.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()                       # dry-run, all three
    ns["main"](apply=True)             # post all three
    ns["main"](apply=True, only=["S6"])  # post one
"""

from __future__ import annotations

import contextlib
import io

import frappe

TARGET_SITE = "szl"
DIR = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration"
GST_ACCOUNT = "GST - SSM"
BRANCHES = {
	"S4": f"{DIR}/import_s4_prices_and_stock.py",
	"S5": f"{DIR}/import_s5_prices_and_stock.py",
	"S6": f"{DIR}/import_s6_prices_and_stock.py",
}


def _plan_for(code: str):
	ns = {}
	path = BRANCHES[code]
	exec(compile(open(path).read(), path, "exec"), ns)
	ns["_assert_site_and_source"]()
	ns["_assert_accounting_context"]()
	with contextlib.redirect_stdout(io.StringIO()):
		plan = ns["build_plan"](ns["parse_s%s_rows" % code[1]]())
	if plan["issues"]:
		frappe.throw(f"{code}: stock plan has {len(plan['issues'])} blocking issues")
	incl = round(sum(r["expected_inclusive_value"] for r in plan["positive"] + plan["negative"]), 2)
	excl = round(sum(r["expected_value"] for r in plan["positive"] + plan["negative"]), 2)
	return ns, incl, excl


def _tag(code: str, posting_date: str) -> str:
	return f"{code}-ONHAND-GST-OPENING-{posting_date}"


def _post(ns, code, total_gst, posting_date):
	tag = _tag(code, posting_date)
	if frappe.db.exists("Journal Entry", {"cheque_no": tag, "docstatus": ["!=", 2]}):
		return "already_posted"
	posted = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabStock Entry` WHERE remarks LIKE %s AND docstatus=1", f"{code}-ONHAND-%"
	)[0][0]
	if not posted:
		frappe.throw(f"{code} opening stock is not posted; refusing to book its GST")
	company, branch, cost_center = ns["COMPANY"], ns["BRANCH"], ns["COST_CENTER"]
	temp = frappe.db.get_value("Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0})
	if not temp:
		frappe.throw(f"No Temporary Opening account under {company}")
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.company = company
	je.posting_date = posting_date
	je.is_opening = "Yes"
	je.cheque_no = tag
	je.cheque_date = posting_date
	je.user_remark = f"{code} opening stock: GST portion backed out of legacy CurCost (back-filled; see import.md)"
	amount, debit = abs(total_gst), total_gst > 0
	common = {"branch": branch, "cost_center": cost_center}
	je.append("accounts", {"account": GST_ACCOUNT, ("debit_in_account_currency" if debit else "credit_in_account_currency"): amount, **common})
	je.append("accounts", {"account": temp, ("credit_in_account_currency" if debit else "debit_in_account_currency"): amount, **common})
	je.insert(ignore_permissions=True)
	je.submit()
	blank = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabJournal Entry Account` WHERE parent=%s AND (IFNULL(cost_center,'')='' OR IFNULL(branch,'')='')",
		je.name,
	)[0][0]
	if blank:
		frappe.throw(f"{je.name} missing dimensions")
	frappe.db.commit()
	return je.name


def main(apply: bool = False, only: list[str] | None = None):
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")
	result = {}
	for code in only or list(BRANCHES):
		ns, incl, excl = _plan_for(code)
		gst = round(incl - excl, 2)
		posting_date = ns["POSTING_DATE"]
		stock_dates = frappe.db.sql(
			"SELECT MIN(posting_date), MAX(posting_date) FROM `tabStock Entry` WHERE remarks LIKE %s AND docstatus=1",
			f"{code}-ONHAND-%",
		)[0]
		row = {
			"branch": ns["BRANCH"],
			"posting_date": posting_date,
			"stock_entry_dates": [str(d) for d in stock_dates],
			"inclusive": incl,
			"exclusive": excl,
			"gst": gst,
			"tag": _tag(code, posting_date),
			"already_posted": bool(
				frappe.db.exists("Journal Entry", {"cheque_no": _tag(code, posting_date), "docstatus": ["!=", 2]})
			),
		}
		if apply:
			row["je"] = _post(ns, code, gst, posting_date)
		result[code] = row
		print(code, row)
	print("TOTAL GST", round(sum(r["gst"] for r in result.values()), 2), "apply" if apply else "DRY_RUN -- nothing posted")
	return result
