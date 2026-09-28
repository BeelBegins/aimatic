from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	permitted_branches = get_permitted_branches()
	validate_branch_access(filters, permitted_branches)
	validate_account(filters)

	opening_balance = get_opening_balance(filters, permitted_branches)
	movements = get_movements(filters, permitted_branches)
	receipts = [row for row in movements if row.movement_type == "Receipt"]
	payments = [row for row in movements if row.movement_type == "Payment"]

	for row in receipts + payments:
		row.amount = flt(row.amount, 2)

	receipts.sort(key=lambda row: (-row.amount, (row.label or "").lower()))
	payments.sort(key=lambda row: (-row.amount, (row.label or "").lower()))

	total_receipts = flt(sum(row.amount for row in receipts), 2)
	total_payments = flt(sum(row.amount for row in payments), 2)
	closing_balance = flt(opening_balance + total_receipts - total_payments, 2)

	data = build_statement_rows(
		opening_balance,
		receipts,
		payments,
		total_receipts,
		total_payments,
		closing_balance,
	)
	for row in data:
		row["currency"] = currency

	message = _(
		"Cash and bank ledger movements from {0} to {1}. "
		"Opening and closing balances use the selected Cash/Bank accounts."
	).format(filters.from_date, filters.to_date)

	return (
		get_columns(currency),
		data,
		message,
		None,
		get_report_summary(opening_balance, total_receipts, total_payments, closing_balance, currency),
	)


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date"))


def get_permitted_branches():
	"""Return explicit Branch User Permissions; None means no branch restriction."""
	permissions = frappe.permissions.get_user_permissions(frappe.session.user)
	branch_permissions = permissions.get("Branch")
	if not branch_permissions:
		return None
	return sorted({permission.doc for permission in branch_permissions if permission.doc})


def validate_branch_access(filters, permitted_branches):
	if permitted_branches is None:
		return
	if filters.get("branch") and filters.branch not in permitted_branches:
		frappe.throw(_("You do not have permission to view this branch."))


def validate_account(filters):
	if not filters.get("account"):
		return

	account = frappe.db.get_value(
		"Account",
		filters.account,
		["company", "account_type", "is_group"],
		as_dict=True,
	)
	if not account or account.company != filters.company or account.account_type not in ("Cash", "Bank") or account.is_group:
		frappe.throw(_("Select a non-group Cash or Bank account belonging to the selected company."))


def _build_conditions(filters, permitted_branches):
	conditions = [
		"ge.`company` = %(company)s",
		"ge.`is_cancelled` = 0",
		"ge.`account` IN ("
		"SELECT `name` FROM `tabAccount` "
		"WHERE `company` = %(company)s AND `is_group` = 0 "
		"AND `account_type` IN ('Cash', 'Bank'))",
	]

	if filters.get("account"):
		conditions.append("ge.`account` = %(account)s")
	if filters.get("branch"):
		conditions.append("ge.`branch` = %(branch)s")
	if permitted_branches is not None:
		if not permitted_branches:
			conditions.append("1 = 0")
		else:
			filters.permitted_branches = tuple(permitted_branches)
			conditions.append("ge.`branch` IN %(permitted_branches)s")

	return conditions


def get_opening_balance(filters, permitted_branches):
	conditions = _build_conditions(filters, permitted_branches)
	conditions.append("ge.`posting_date` < %(from_date)s")

	# nosemgrep: values are parameterized through frappe.db.sql.
	result = frappe.db.sql(
		f"""
		SELECT COALESCE(SUM(ge.`debit` - ge.`credit`), 0)
		FROM `tabGL Entry` ge
		WHERE {' AND '.join(conditions)}
		""",
		filters,
	)[0][0]
	return flt(result, 2)


def get_movements(filters, permitted_branches):
	conditions = _build_conditions(filters, permitted_branches)
	conditions.extend(
		[
			"ge.`posting_date` BETWEEN %(from_date)s AND %(to_date)s",
			"(ge.`debit` > 0 OR ge.`credit` > 0)",
		]
	)

	# Grouping happens at the database after each cash movement is assigned to
	# Receipt or Payment. This keeps the report compact for high-volume POS data.
	# nosemgrep: values are parameterized through frappe.db.sql.
	return frappe.db.sql(
		f"""
		SELECT movement_type, label, SUM(amount) AS amount, COUNT(*) AS entry_count
		FROM (
			SELECT
				CASE WHEN ge.`debit` > 0 THEN 'Receipt' ELSE 'Payment' END AS movement_type,
				COALESCE(
					NULLIF(TRIM(ge.`party`), ''),
					NULLIF(TRIM(ge.`against`), ''),
					NULLIF(TRIM(ge.`account`), '')
				) AS label,
				CASE WHEN ge.`debit` > 0 THEN ge.`debit` ELSE ge.`credit` END AS amount
			FROM `tabGL Entry` ge
			WHERE {' AND '.join(conditions)}
		) movements
		GROUP BY movement_type, label
		ORDER BY movement_type, amount DESC, label
		""",
		filters,
		as_dict=True,
	)


def build_statement_rows(
	opening_balance,
	receipts,
	payments,
	total_receipts,
	total_payments,
	closing_balance,
):
	data = [
		{
			"receipt_particular": _("Opening Balance"),
			"receipt_amount": opening_balance,
			"row_type": "opening_balance",
		}
	]

	for index in range(max(len(receipts), len(payments))):
		receipt = receipts[index] if index < len(receipts) else None
		payment = payments[index] if index < len(payments) else None
		data.append(
			{
				"receipt_particular": receipt.label if receipt else "",
				"receipt_amount": receipt.amount if receipt else None,
				"payment_particular": payment.label if payment else "",
				"payment_amount": payment.amount if payment else None,
				"row_type": "movement",
			}
		)

	data.extend(
		[
			{
				"receipt_particular": _("Total Receipts"),
				"receipt_amount": total_receipts,
				"row_type": "total_receipts",
			},
			{
				"payment_particular": _("Total Payments & Expenses"),
				"payment_amount": total_payments,
				"row_type": "total_payments",
			},
			{
				"receipt_particular": _("Closing Balance"),
				"receipt_amount": closing_balance,
				"row_type": "closing_balance",
			},
		]
	)
	return data


def get_columns(currency):
	return [
		{
			"label": _("Receipts"),
			"fieldname": "receipt_particular",
			"fieldtype": "Data",
			"width": 260,
		},
		{
			"label": _("Amount"),
			"fieldname": "receipt_amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Payments"),
			"fieldname": "payment_particular",
			"fieldtype": "Data",
			"width": 310,
		},
		{
			"label": _("Amount"),
			"fieldname": "payment_amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 130,
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Data",
			"hidden": 1,
			"default": currency,
		},
	]


def get_report_summary(opening_balance, total_receipts, total_payments, closing_balance, currency):
	return [
		{
			"label": _("Opening Balance"),
			"value": opening_balance,
			"indicator": "Blue",
			"datatype": "Currency",
			"currency": currency,
		},
		{
			"label": _("Total Receipts"),
			"value": total_receipts,
			"indicator": "Green",
			"datatype": "Currency",
			"currency": currency,
		},
		{
			"label": _("Total Payments"),
			"value": total_payments,
			"indicator": "Orange",
			"datatype": "Currency",
			"currency": currency,
		},
		{
			"label": _("Closing Balance"),
			"value": closing_balance,
			"indicator": "Green" if closing_balance >= 0 else "Red",
			"datatype": "Currency",
			"currency": currency,
		},
	]
