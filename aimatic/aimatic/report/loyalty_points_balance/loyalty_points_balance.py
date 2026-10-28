from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from aimatic.loyalty.reporting import check_loyalty_report_access


def execute(filters=None):
	filters = frappe._dict(filters or {})
	check_loyalty_report_access()
	validate_filters(filters)

	conditions = [
		"lpe.company = %(company)s",
		"lpe.posting_date <= %(as_of_date)s",
	]
	params = {"company": filters.company, "as_of_date": filters.as_of_date}
	if filters.get("customer"):
		conditions.append("lpe.customer = %(customer)s")
		params["customer"] = filters.customer
	if filters.get("loyalty_program"):
		conditions.append("lpe.loyalty_program = %(loyalty_program)s")
		params["loyalty_program"] = filters.loyalty_program

	rows = frappe.db.sql(
		f"""
		SELECT
			lpe.customer,
			COALESCE(c.customer_name, lpe.customer) AS customer_name,
			lpe.loyalty_program,
			SUM(CASE WHEN lpe.loyalty_points > 0 THEN lpe.loyalty_points ELSE 0 END) AS points_earned,
			SUM(CASE WHEN lpe.loyalty_points < 0 THEN -lpe.loyalty_points ELSE 0 END) AS points_redeemed,
			SUM(lpe.loyalty_points) AS points_available,
			SUM(CASE WHEN lpe.loyalty_points < 0 THEN 1 ELSE 0 END) AS redemption_entries
		FROM `tabLoyalty Point Entry` lpe
		LEFT JOIN `tabCustomer` c ON c.name = lpe.customer
		WHERE {' AND '.join(conditions)}
		GROUP BY lpe.customer, COALESCE(c.customer_name, lpe.customer), lpe.loyalty_program
		HAVING points_earned <> 0 OR points_redeemed <> 0 OR points_available <> 0
		ORDER BY points_available DESC, customer_name
		""",
		params,
		as_dict=True,
	)
	data = [
		{
			"customer": row.customer,
			"customer_name": row.customer_name,
			"loyalty_program": row.loyalty_program,
			"points_earned": cint(row.points_earned),
			"points_redeemed": cint(row.points_redeemed),
			"points_available": cint(row.points_available),
			"redemption_entries": cint(row.redemption_entries),
		}
		for row in rows
	]
	return get_columns(), data, None, None, get_summary(data)


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))
	if not filters.get("as_of_date"):
		frappe.throw(_("As of Date is mandatory"))
	if getdate(filters.as_of_date) > getdate(frappe.utils.today()):
		frappe.throw(_("As of Date cannot be in the future"))


def get_columns():
	return [
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
		{"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 220},
		{"label": _("Loyalty Program"), "fieldname": "loyalty_program", "fieldtype": "Link", "options": "Loyalty Program", "width": 180},
		{"label": _("Points Earned"), "fieldname": "points_earned", "fieldtype": "Int", "width": 120},
		{"label": _("Points Redeemed"), "fieldname": "points_redeemed", "fieldtype": "Int", "width": 130},
		{"label": _("Points Available"), "fieldname": "points_available", "fieldtype": "Int", "width": 130},
		{"label": _("Redemption Entries"), "fieldname": "redemption_entries", "fieldtype": "Int", "width": 130},
	]


def get_summary(data):
	return [
		{"label": _("Points Earned"), "value": sum(row["points_earned"] for row in data), "indicator": "Blue", "datatype": "Int"},
		{"label": _("Points Redeemed"), "value": sum(row["points_redeemed"] for row in data), "indicator": "Orange", "datatype": "Int"},
		{"label": _("Points Available"), "value": sum(row["points_available"] for row in data), "indicator": "Green", "datatype": "Int"},
	]
