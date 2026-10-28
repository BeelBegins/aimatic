from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from aimatic.loyalty.reporting import (
	HONORED_LOYALTY_CONDITION,
	check_loyalty_report_access,
	visible_branches,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	check_loyalty_report_access()
	validate_filters(filters)

	branch_filter = visible_branches(filters.company, filters.get("branch"))
	if branch_filter is not None and not branch_filter:
		return get_columns(), [], _("No branches are visible to your account."), None, get_summary([])

	conditions = [
		"pi.docstatus = 1",
		"pi.company = %(company)s",
		"pi.posting_date BETWEEN %(from_date)s AND %(to_date)s",
		f"({HONORED_LOYALTY_CONDITION})",
	]
	params = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
	}
	if branch_filter is not None:
		conditions.append("COALESCE(pi.branch, pp.branch) IN %(branches)s")
		params["branches"] = tuple(branch_filter)
	if filters.get("pos_profile"):
		conditions.append("pi.pos_profile = %(pos_profile)s")
		params["pos_profile"] = filters.pos_profile

	rows = frappe.db.sql(
		f"""
		SELECT
			pi.posting_date AS date,
			COALESCE(pi.branch, pp.branch) AS branch,
			pi.pos_profile AS counter,
			COUNT(*) AS redemption_count,
			SUM(pi.loyalty_points) AS points_redeemed,
			SUM(pi.loyalty_amount) AS amount_redeemed
		FROM `tabPOS Invoice` pi
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		WHERE {' AND '.join(conditions)}
		GROUP BY pi.posting_date, COALESCE(pi.branch, pp.branch), pi.pos_profile
		ORDER BY pi.posting_date DESC, branch, counter
		""",
		params,
		as_dict=True,
	)
	data = [
		{
			"date": row.date,
			"branch": row.branch or _("Unassigned"),
			"counter": row.counter,
			"redemption_count": cint(row.redemption_count),
			"points_redeemed": cint(row.points_redeemed),
			"amount_redeemed": flt(row.amount_redeemed, 2),
		}
		for row in rows
	]
	return get_columns(), data, None, None, get_summary(data)


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date"))


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Data", "width": 180},
		{"label": _("Counter"), "fieldname": "counter", "fieldtype": "Link", "options": "POS Profile", "width": 180},
		{"label": _("Redemptions"), "fieldname": "redemption_count", "fieldtype": "Int", "width": 110},
		{"label": _("Points Redeemed"), "fieldname": "points_redeemed", "fieldtype": "Int", "width": 130},
		{"label": _("Amount Redeemed"), "fieldname": "amount_redeemed", "fieldtype": "Currency", "width": 140},
	]


def get_summary(data):
	return [
		{"label": _("Redemptions"), "value": sum(row["redemption_count"] for row in data), "indicator": "Blue", "datatype": "Int"},
		{"label": _("Points Redeemed"), "value": sum(row["points_redeemed"] for row in data), "indicator": "Orange", "datatype": "Int"},
		{"label": _("Amount Redeemed"), "value": flt(sum(row["amount_redeemed"] for row in data), 2), "indicator": "Green", "datatype": "Currency"},
	]
