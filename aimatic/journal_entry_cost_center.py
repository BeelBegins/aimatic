"""Journal Entry cost center visibility and branch alignment.

ERPNext hides Accounting Dimensions on Journal Entry Account via a Property
Setter and silently stamps Company.cost_center (Head Office). Accountants then
post branch expenses with the right Branch but the wrong Cost Center.

Rules:
- P&L rows must have a Cost Center (server validates).
- When Branch is set, Cost Center follows Branch.cost_center unless the user
  already chose a non-company-default Cost Center.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint


def _company_default_cost_center(company: str | None) -> str | None:
	if not company:
		return None
	return frappe.get_cached_value("Company", company, "cost_center")


def _branch_cost_center(branch: str | None) -> str | None:
	if not branch:
		return None
	return frappe.get_cached_value("Branch", branch, "cost_center")


def _account_report_type(account: str | None) -> str | None:
	if not account:
		return None
	return frappe.get_cached_value("Account", account, "report_type")


def sync_journal_entry_cost_centers(doc, method=None):
	"""before_validate: align Cost Center with Branch; require it on P&L rows."""
	if getattr(doc, "doctype", None) != "Journal Entry":
		return
	if cint(getattr(doc, "docstatus", 0)) != 0:
		return

	company_cc = _company_default_cost_center(getattr(doc, "company", None))
	missing = []

	for row in doc.get("accounts") or []:
		branch_cc = _branch_cost_center(row.get("branch"))
		current = (row.get("cost_center") or "").strip() or None

		if branch_cc and (not current or current == company_cc):
			row.cost_center = branch_cc
			current = branch_cc

		report_type = _account_report_type(row.get("account"))
		if report_type == "Profit and Loss" and not current:
			missing.append(row.idx)

	if missing:
		frappe.throw(
			_(
				"Cost Center is required for Profit and Loss accounts. "
				"Missing on Journal Entry Account row(s): {0}"
			).format(", ".join(str(i) for i in missing))
		)


def dry_run_cost_center_backfill(
	*,
	owner: str | None = None,
	company_cost_center: str | None = None,
) -> dict:
	"""List submitted JE rows whose Branch implies a different Cost Center."""
	conditions = ["je.docstatus = 1", "ifnull(jea.branch, '') != ''"]
	values: list = []
	if owner:
		conditions.append("je.owner = %s")
		values.append(owner)
	if company_cost_center:
		conditions.append("jea.cost_center = %s")
		values.append(company_cost_center)

	rows = frappe.db.sql(
		f"""
		select je.name as journal_entry, je.posting_date, je.owner,
		       jea.name as row_name, jea.idx, jea.account, jea.branch,
		       jea.cost_center as current_cost_center,
		       b.cost_center as branch_cost_center
		from `tabJournal Entry` je
		join `tabJournal Entry Account` jea on jea.parent = je.name
		join `tabBranch` b on b.name = jea.branch
		where {" and ".join(conditions)}
		  and ifnull(b.cost_center, '') != ''
		  and ifnull(jea.cost_center, '') != b.cost_center
		order by je.posting_date, je.name, jea.idx
		""",
		values,
		as_dict=True,
	)
	jes = sorted({r.journal_entry for r in rows})
	return {
		"row_count": len(rows),
		"journal_entry_count": len(jes),
		"journal_entries": jes,
		"rows": rows,
	}


def backfill_cost_centers_from_branch(
	*,
	owner: str | None = None,
	company_cost_center: str | None = None,
	dry_run: bool = True,
) -> dict:
	"""Set JE Account + matching GL Entry cost_center from Branch.cost_center."""
	report = dry_run_cost_center_backfill(
		owner=owner, company_cost_center=company_cost_center
	)
	if dry_run:
		report["dry_run"] = True
		report["updated_rows"] = 0
		report["updated_gl"] = 0
		return report

	updated_rows = 0
	updated_gl = 0
	for row in report["rows"]:
		frappe.db.set_value(
			"Journal Entry Account",
			row.row_name,
			"cost_center",
			row.branch_cost_center,
			update_modified=False,
		)
		updated_rows += 1
		frappe.db.sql(
			"""
			update `tabGL Entry`
			set cost_center = %s
			where voucher_type = 'Journal Entry'
			  and voucher_no = %s
			  and account = %s
			  and ifnull(branch, '') = %s
			  and is_cancelled = 0
			  and ifnull(cost_center, '') != %s
			""",
			(
				row.branch_cost_center,
				row.journal_entry,
				row.account,
				row.branch,
				row.branch_cost_center,
			),
		)
		updated_gl += cint(frappe.db._cursor.rowcount)
	frappe.db.commit()
	report["dry_run"] = False
	report["updated_rows"] = updated_rows
	report["updated_gl"] = updated_gl
	# Drop bulky row dump from live result unless small.
	if len(report["rows"]) > 50:
		report["rows"] = report["rows"][:20]
		report["rows_truncated"] = True
	return report
