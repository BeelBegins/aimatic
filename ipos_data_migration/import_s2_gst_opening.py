"""S2 follow-up to import_s2_prices_and_stock.py: post the GST portion that
was backed out of the tax-INCLUSIVE CurCost before it became the Stock Entry
valuation rate. Same handling as import_szl_s1_gst_opening.py /
import_s7_gst_opening.py (user: "s1 wala kaam he hona chahiye", 2026-10-11).

One Opening Entry Journal Entry: Dr ``GST - SSM`` / Cr ``Temporary Opening``,
branch and cost center stamped, idempotent by TAG (cheque_no).

Unlike the S7 script, GST is computed from the exact plan rows the S2 stock
import posts (imported from import_s2_prices_and_stock.py), so the two cannot
drift: GST = sum(inclusive value) - sum(exclusive value) over posted rows.
Negative-stock rows reduce it, as in S1.

Run AFTER the S2 stock import, then verify the JE total equals the dry-run.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_gst_opening.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)  # post on szl, only after approval + backup
"""

from __future__ import annotations

import frappe

TARGET_SITE = "szl"
STOCK_SCRIPT = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_prices_and_stock.py"
GST_ACCOUNT = "GST - SSM"
TAG = "S2-ONHAND-GST-OPENING-2026-10-11"


def _load_stock_module():
	ns = {}
	exec(compile(open(STOCK_SCRIPT).read(), STOCK_SCRIPT, "exec"), ns)
	return ns


def main(apply: bool = False):
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")
	if frappe.db.exists("Journal Entry", {"cheque_no": TAG, "docstatus": ["!=", 2]}):
		print("Already run -- Journal Entry with this tag already exists. Nothing to do.")
		return

	ns = _load_stock_module()
	ns["_assert_site_and_source"]()
	ns["_assert_accounting_context"]()
	plan = ns["build_plan"](ns["parse_rows"]())
	if plan["issues"]:
		frappe.throw(f"Stock plan still has {len(plan['issues'])} blocking issues; fix them first")

	rows = plan["positive"] + plan["negative"]
	inclusive = sum(r["expected_inclusive_value"] for r in rows)
	exclusive = sum(r["expected_value"] for r in rows)
	total_gst = round(inclusive - exclusive, 2)
	warehouse, branch, cost_center = ns["WAREHOUSE"], ns["BRANCH"], ns["COST_CENTER"]
	posting_date, company = ns["POSTING_DATE"], ns["COMPANY"]
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"rows": len(rows),
			"inclusive_total": round(inclusive, 2),
			"exclusive_total": round(exclusive, 2),
			"total_gst": total_gst,
			"warehouse": warehouse,
			"posting_date": posting_date,
		}
	)
	if not apply:
		print("DRY_RUN -- no Journal Entry created.")
		return {"total_gst": total_gst, "rows": len(rows)}
	if not total_gst:
		print("Total GST rounds to 0 -- nothing to post.")
		return {"total_gst": 0}

	# The stock must already be posted, otherwise this GST has nothing to offset.
	posted = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabStock Entry` WHERE remarks LIKE 'S2-ONHAND-%%' AND docstatus=1"
	)[0][0]
	if not posted:
		frappe.throw("S2 opening stock is not posted yet; post it before the GST opening")

	temp_account = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
	)
	if not temp_account:
		frappe.throw(f"No 'Temporary Opening' account found under {company}.")

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.company = company
	je.posting_date = posting_date
	je.is_opening = "Yes"
	je.cheque_no = TAG
	je.cheque_date = posting_date
	je.user_remark = (
		"S2 opening stock: GST portion backed out of legacy CurCost before stock valuation (see import.md)"
	)
	amount = abs(total_gst)
	debit_gst = total_gst > 0
	common = {"branch": branch, "cost_center": cost_center}
	je.append(
		"accounts",
		{"account": GST_ACCOUNT, ("debit_in_account_currency" if debit_gst else "credit_in_account_currency"): amount, **common},
	)
	je.append(
		"accounts",
		{"account": temp_account, ("credit_in_account_currency" if debit_gst else "debit_in_account_currency"): amount, **common},
	)
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	print(f"Created and submitted {je.name} for {total_gst}")
	return {"total_gst": total_gst, "je": je.name}
