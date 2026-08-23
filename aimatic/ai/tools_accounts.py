from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from aimatic.ai.tools import _get_date_range, _resolve_company


def get_payables_aging(limit: int = 10) -> dict:
	"""Age each supplier's outstanding GL balance into 4 buckets (0-30, 31-60, 61-90, 90+ days)."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	as_of_date = getdate(today())

	# Fetch all relevant GL Entries for suppliers
	gl_entries = frappe.db.sql(
		"""
        SELECT ge.account, ge.party, ge.debit, ge.credit, ge.posting_date
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.party_type = 'Supplier'
          AND acc.is_group = 0
        """,
		company,
		as_dict=True,
	)

	supplier_buckets: dict[str, dict] = {}

	for row in gl_entries:
		party = row.party
		amount = flt(row.credit) - flt(row.debit)
		if amount == 0:
			continue
		posting_date = getdate(row.posting_date)
		days_diff = (as_of_date - posting_date).days

		if days_diff <= 30:
			bucket_key = "0-30"
		elif days_diff <= 60:
			bucket_key = "31-60"
		elif days_diff <= 90:
			bucket_key = "61-90"
		else:
			bucket_key = "90+"

		if party not in supplier_buckets:
			supplier_buckets[party] = {
				"supplier": party,
				"outstanding_amount": 0.0,
				"bucket_0_30": 0.0,
				"bucket_31_60": 0.0,
				"bucket_61_90": 0.0,
				"bucket_90_plus": 0.0,
			}
		supplier_buckets[party]["outstanding_amount"] = (
			flt(supplier_buckets[party]["outstanding_amount"]) + amount
		)
		supplier_buckets[party][f"bucket_{bucket_key.replace('-', '_').replace('+', '_plus')}"] = (
			flt(supplier_buckets[party][f"bucket_{bucket_key.replace('-', '_').replace('+', '_plus')}"])
			+ amount
		)

	# Only suppliers with a positive NET balance count as "payable" - a supplier
	# whose rows net to a credit/prepaid position is excluded entirely (from both
	# the totals and the aggregate buckets below), not just from top_suppliers,
	# so buckets always sum to exactly total_outstanding_amount.
	positive_suppliers = [s for s in supplier_buckets.values() if flt(s["outstanding_amount"]) > 0]
	positive_suppliers.sort(key=lambda x: flt(x["outstanding_amount"]), reverse=True)
	top_suppliers = positive_suppliers[:limit]

	total_outstanding = sum(flt(s["outstanding_amount"]) for s in positive_suppliers)
	buckets = {
		"0-30": sum(flt(s["bucket_0_30"]) for s in positive_suppliers),
		"31-60": sum(flt(s["bucket_31_60"]) for s in positive_suppliers),
		"61-90": sum(flt(s["bucket_61_90"]) for s in positive_suppliers),
		"90+": sum(flt(s["bucket_90_plus"]) for s in positive_suppliers),
	}

	return {
		"company": company,
		"currency": currency,
		"as_of_date": as_of_date,
		"buckets": buckets,
		"total_outstanding_amount": total_outstanding,
		"top_suppliers": top_suppliers,
	}


def get_receivables_aging(limit: int = 10) -> dict:
	"""Age each customer's outstanding GL balance into 4 buckets (0-30, 31-60, 61-90, 90+ days)."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	as_of_date = getdate(today())

	gl_entries = frappe.db.sql(
		"""
        SELECT ge.account, ge.party, ge.debit, ge.credit, ge.posting_date
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.party_type = 'Customer'
          AND acc.is_group = 0
        """,
		company,
		as_dict=True,
	)

	customer_buckets: dict[str, dict] = {}

	for row in gl_entries:
		party = row.party
		amount = flt(row.debit) - flt(row.credit)  # Receivable: debit - credit
		if amount == 0:
			continue
		posting_date = getdate(row.posting_date)
		days_diff = (as_of_date - posting_date).days

		if days_diff <= 30:
			bucket_key = "0-30"
		elif days_diff <= 60:
			bucket_key = "31-60"
		elif days_diff <= 90:
			bucket_key = "61-90"
		else:
			bucket_key = "90+"

		if party not in customer_buckets:
			customer_buckets[party] = {
				"customer": party,
				"customer_name": frappe.get_cached_value("Customer", party, "customer_name") or party,
				"outstanding_amount": 0.0,
				"bucket_0_30": 0.0,
				"bucket_31_60": 0.0,
				"bucket_61_90": 0.0,
				"bucket_90_plus": 0.0,
			}
		customer_buckets[party]["outstanding_amount"] = (
			flt(customer_buckets[party]["outstanding_amount"]) + amount
		)
		customer_buckets[party][f"bucket_{bucket_key.replace('-', '_').replace('+', '_plus')}"] = (
			flt(customer_buckets[party][f"bucket_{bucket_key.replace('-', '_').replace('+', '_plus')}"])
			+ amount
		)

	# Same reasoning as get_payables_aging: only positive-net-balance customers
	# count, and buckets are re-derived from that filtered set so they always
	# sum to exactly total_outstanding_amount.
	positive_customers = [c for c in customer_buckets.values() if flt(c["outstanding_amount"]) > 0]
	positive_customers.sort(key=lambda x: flt(x["outstanding_amount"]), reverse=True)
	top_customers = positive_customers[:limit]

	total_outstanding = sum(flt(c["outstanding_amount"]) for c in positive_customers)
	buckets = {
		"0-30": sum(flt(c["bucket_0_30"]) for c in positive_customers),
		"31-60": sum(flt(c["bucket_31_60"]) for c in positive_customers),
		"61-90": sum(flt(c["bucket_61_90"]) for c in positive_customers),
		"90+": sum(flt(c["bucket_90_plus"]) for c in positive_customers),
	}

	return {
		"company": company,
		"currency": currency,
		"as_of_date": as_of_date,
		"buckets": buckets,
		"total_outstanding_amount": total_outstanding,
		"top_customers": top_customers,
	}


def get_cash_and_bank_balance() -> dict:
	"""Point-in-time balance of every Cash/Bank account (account_type Cash or Bank)."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	as_of_date = getdate(today())

	accounts = frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "account_type": ["in", ["Cash", "Bank"]]},
		fields=["name", "account_type"],
	)

	if not accounts:
		return {
			"company": company,
			"currency": currency,
			"as_of_date": as_of_date,
			"total_balance": 0.0,
			"accounts": [],
		}

	account_names = [a.name for a in accounts]
	placeholders = ", ".join(["%s"] * len(account_names))

	gl_data = frappe.db.sql(
		f"""
        SELECT ge.account, SUM(ge.debit - ge.credit) as balance
        FROM `tabGL Entry` ge
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.posting_date <= %s
          AND ge.account IN ({placeholders})
        GROUP BY ge.account
        """,
		[company, as_of_date] + account_names,
		as_dict=True,
	)

	balance_map = {row.account: flt(row.balance) for row in gl_data}

	result_accounts = []
	total_balance = 0.0
	for acc in accounts:
		bal = balance_map.get(acc.name, 0.0)
		result_accounts.append({"account": acc.name, "account_type": acc.account_type, "balance": bal})
		total_balance += bal

	result_accounts.sort(key=lambda x: x["balance"], reverse=True)

	return {
		"company": company,
		"currency": currency,
		"as_of_date": as_of_date,
		"total_balance": total_balance,
		"accounts": result_accounts,
	}


def get_profit_and_loss_overview(date_from: str | None = None, date_to: str | None = None) -> dict:
	"""Total income, expense, net profit, and net margin for a date range."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_from, date_to = _get_date_range(date_from, date_to)

	# Income: root_type='Income', normal credit balance (credit - debit)
	income = (
		frappe.db.sql(
			"""
        SELECT SUM(ge.credit - ge.debit) as total
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND acc.is_group = 0
          AND acc.root_type = 'Income'
        """,
			(company, date_from, date_to),
			as_dict=True,
		)[0].total
		or 0.0
	)

	# Expense: root_type='Expense', normal debit balance (debit - credit)
	expense = (
		frappe.db.sql(
			"""
        SELECT SUM(ge.debit - ge.credit) as total
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND acc.is_group = 0
          AND acc.root_type = 'Expense'
        """,
			(company, date_from, date_to),
			as_dict=True,
		)[0].total
		or 0.0
	)

	total_income = flt(income)
	total_expense = flt(expense)
	net_profit = total_income - total_expense
	net_margin_pct = (net_profit / total_income * 100) if total_income else 0.0

	return {
		"company": company,
		"currency": currency,
		"date_from": date_from,
		"date_to": date_to,
		"total_income": total_income,
		"total_expense": total_expense,
		"net_profit": net_profit,
		"net_margin_pct": net_margin_pct,
	}


def get_expense_breakdown(date_from: str | None = None, date_to: str | None = None, limit: int = 10) -> dict:
	"""Top N expense accounts by amount in date range, plus total expense across all expense accounts."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_from, date_to = _get_date_range(date_from, date_to)

	# Total expense (all expense accounts)
	total_expense_row = frappe.db.sql(
		"""
        SELECT SUM(ge.debit - ge.credit) as total
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND acc.is_group = 0
          AND acc.root_type = 'Expense'
        """,
		(company, date_from, date_to),
		as_dict=True,
	)
	total_expense = flt(total_expense_row[0].total) if total_expense_row else 0.0

	# Top N expense accounts
	top_accounts = frappe.db.sql(
		"""
        SELECT ge.account, SUM(ge.debit - ge.credit) as amount
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND acc.is_group = 0
          AND acc.root_type = 'Expense'
        GROUP BY ge.account
        ORDER BY amount DESC
        LIMIT %s
        """,
		(company, date_from, date_to, limit),
		as_dict=True,
	)

	return {
		"company": company,
		"currency": currency,
		"date_from": date_from,
		"date_to": date_to,
		"total_expense": total_expense,
		"top_accounts": [{"account": row.account, "amount": flt(row.amount)} for row in top_accounts],
	}


def get_trial_balance_summary(date_from: str | None = None, date_to: str | None = None) -> dict:
	"""Cumulative balances for Asset, Liability, Equity (up to date_to) and period balances for Income, Expense (date_from to date_to)."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_from, date_to = _get_date_range(date_from, date_to)

	def get_cumulative_balance(root_type: str, sign: int) -> float:
		# sign: +1 for Asset (debit-credit), -1 for Liability/Equity (credit-debit)
		res = frappe.db.sql(
			"""
            SELECT SUM(ge.debit - ge.credit) * %s as total
            FROM `tabGL Entry` ge
            JOIN `tabAccount` acc ON acc.name = ge.account
            WHERE ge.company = %s
              AND ge.is_cancelled = 0
              AND ge.posting_date <= %s
              AND acc.is_group = 0
              AND acc.root_type = %s
            """,
			(sign, company, date_to, root_type),
			as_dict=True,
		)
		return flt(res[0].total) if res else 0.0

	def get_period_balance(root_type: str, sign: int) -> float:
		res = frappe.db.sql(
			"""
            SELECT SUM(ge.debit - ge.credit) * %s as total
            FROM `tabGL Entry` ge
            JOIN `tabAccount` acc ON acc.name = ge.account
            WHERE ge.company = %s
              AND ge.is_cancelled = 0
              AND ge.posting_date BETWEEN %s AND %s
              AND acc.is_group = 0
              AND acc.root_type = %s
            """,
			(sign, company, date_from, date_to, root_type),
			as_dict=True,
		)
		return flt(res[0].total) if res else 0.0

	asset_balance = get_cumulative_balance("Asset", 1)
	liability_balance = get_cumulative_balance("Liability", -1)
	equity_balance = get_cumulative_balance("Equity", -1)
	income_balance = get_period_balance("Income", -1)  # Income: credit - debit
	expense_balance = get_period_balance("Expense", 1)  # Expense: debit - credit

	balance_check = asset_balance - (liability_balance + equity_balance)

	return {
		"company": company,
		"currency": currency,
		"date_from": date_from,
		"date_to": date_to,
		"as_of_date": date_to,
		"asset_balance": asset_balance,
		"liability_balance": liability_balance,
		"equity_balance": equity_balance,
		"income_balance": income_balance,
		"expense_balance": expense_balance,
		"balance_check": balance_check,
	}


def get_tax_liability_overview(date_from: str | None = None, date_to: str | None = None) -> dict:
	"""Tax account balances (account_type='Tax') for a date range."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_from, date_to = _get_date_range(date_from, date_to)

	tax_accounts = frappe.get_all(
		"Account",
		filters={"company": company, "is_group": 0, "account_type": "Tax"},
		fields=["name"],
	)

	if not tax_accounts:
		return {
			"company": company,
			"currency": currency,
			"date_from": date_from,
			"date_to": date_to,
			"total_tax_balance": 0.0,
			"tax_accounts": [],
		}

	account_names = [a.name for a in tax_accounts]
	placeholders = ", ".join(["%s"] * len(account_names))

	rows = frappe.db.sql(
		f"""
        SELECT ge.account, SUM(ge.credit - ge.debit) as balance
        FROM `tabGL Entry` ge
        WHERE ge.company = %s
          AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND ge.account IN ({placeholders})
        GROUP BY ge.account
        """,
		[company, date_from, date_to] + account_names,
		as_dict=True,
	)

	tax_accounts_list = []
	total_tax_balance = 0.0
	for row in rows:
		bal = flt(row.balance)
		tax_accounts_list.append({"account": row.account, "balance": bal})
		total_tax_balance += bal

	return {
		"company": company,
		"currency": currency,
		"date_from": date_from,
		"date_to": date_to,
		"total_tax_balance": total_tax_balance,
		"tax_accounts": tax_accounts_list,
	}


def get_payment_entry_summary(date_from: str | None = None, date_to: str | None = None) -> dict:
	"""Payment Entry summary: total received, total paid, net cash flow, and breakdown by mode of payment."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_from, date_to = _get_date_range(date_from, date_to)

	# Total received (payment_type='Receive')
	total_received = (
		frappe.db.sql(
			"""
        SELECT SUM(received_amount) as total
        FROM `tabPayment Entry`
        WHERE company = %s
          AND docstatus = 1
          AND payment_type = 'Receive'
          AND posting_date BETWEEN %s AND %s
        """,
			(company, date_from, date_to),
			as_dict=True,
		)[0].total
		or 0.0
	)

	# Total paid (payment_type='Pay')
	total_paid = (
		frappe.db.sql(
			"""
        SELECT SUM(paid_amount) as total
        FROM `tabPayment Entry`
        WHERE company = %s
          AND docstatus = 1
          AND payment_type = 'Pay'
          AND posting_date BETWEEN %s AND %s
        """,
			(company, date_from, date_to),
			as_dict=True,
		)[0].total
		or 0.0
	)

	# Breakdown by mode_of_payment and payment_type
	breakdown = frappe.db.sql(
		"""
        SELECT mode_of_payment, payment_type, SUM(paid_amount) as amount, COUNT(*) as txn_count
        FROM `tabPayment Entry`
        WHERE company = %s
          AND docstatus = 1
          AND posting_date BETWEEN %s AND %s
        GROUP BY mode_of_payment, payment_type
        ORDER BY amount DESC
        """,
		(company, date_from, date_to),
		as_dict=True,
	)

	return {
		"company": company,
		"currency": currency,
		"date_from": date_from,
		"date_to": date_to,
		"total_received": flt(total_received),
		"total_paid": flt(total_paid),
		"net_cash_flow": flt(total_received) - flt(total_paid),
		"by_mode": [
			{
				"mode_of_payment": row.mode_of_payment,
				"payment_type": row.payment_type,
				"amount": flt(row.amount),
				"txn_count": cint(row.txn_count),
			}
			for row in breakdown
		],
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_payables_aging",
			"description": "Get aged payables buckets (0-30, 31-60, 61-90, 90+ days) and top suppliers by outstanding amount. Use for questions about overdue payables, supplier aging, or how much is owed in each aging bucket.",
			"parameters": {
				"type": "object",
				"properties": {
					"limit": {
						"type": "integer",
						"description": "Max suppliers to return, default 10, max 30.",
					},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_receivables_aging",
			"description": "Get aged receivables buckets (0-30, 31-60, 61-90, 90+ days) and top customers by outstanding amount. Use for questions about overdue receivables, customer aging, or collections.",
			"parameters": {
				"type": "object",
				"properties": {
					"limit": {
						"type": "integer",
						"description": "Max customers to return, default 10, max 30.",
					},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_cash_and_bank_balance",
			"description": "Get current cash and bank balances across all Cash/Bank accounts. Use for questions about cash position, liquidity, or bank balances.",
			"parameters": {"type": "object", "properties": {}},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_profit_and_loss_overview",
			"description": "Get total income, total expense, net profit, and net margin for a date range. Use for questions about profitability, P&L, or financial performance.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": 'Start date, YYYY-MM-DD. Defaults to date_to if omitted - resolve phrases like "this month" or "this year" into a concrete date yourself before calling.',
					},
					"date_to": {"type": "string", "description": "End date (YYYY-MM-DD), defaults to today."},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_expense_breakdown",
			"description": "Get top expense accounts by amount for a date range, plus total expense. Use for questions about biggest expenses, cost breakdown, or spending analysis.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": 'Start date, YYYY-MM-DD. Defaults to date_to if omitted - resolve phrases like "this month" or "this year" into a concrete date yourself before calling.',
					},
					"date_to": {"type": "string", "description": "End date (YYYY-MM-DD), defaults to today."},
					"limit": {
						"type": "integer",
						"description": "Max accounts to return, default 10, max 30.",
					},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_trial_balance_summary",
			"description": "Get trial balance summary: cumulative Asset/Liability/Equity balances (up to date_to) and period Income/Expense balances (date_from to date_to). Use for questions about balance sheet, trial balance, or accounting equation check.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": 'Start date for Income/Expense, YYYY-MM-DD. Defaults to date_to if omitted - resolve phrases like "this month" or "this year" into a concrete date yourself before calling.',
					},
					"date_to": {
						"type": "string",
						"description": "End date for all (YYYY-MM-DD), defaults to today.",
					},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_tax_liability_overview",
			"description": "Get tax account balances (account_type='Tax') for a date range. Use for questions about tax liability, GST/VAT payable, or tax collected.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": 'Start date, YYYY-MM-DD. Defaults to date_to if omitted - resolve phrases like "this month" or "this year" into a concrete date yourself before calling.',
					},
					"date_to": {"type": "string", "description": "End date (YYYY-MM-DD), defaults to today."},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_payment_entry_summary",
			"description": "Get payment entry summary: total received, total paid, net cash flow, and breakdown by mode of payment. Use for questions about cash flow, payments received, payments made, or payment modes.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": 'Start date, YYYY-MM-DD. Defaults to date_to if omitted - resolve phrases like "this month" or "this year" into a concrete date yourself before calling.',
					},
					"date_to": {"type": "string", "description": "End date (YYYY-MM-DD), defaults to today."},
				},
			},
		},
	},
]

TOOL_DISPATCH = {
	"get_payables_aging": get_payables_aging,
	"get_receivables_aging": get_receivables_aging,
	"get_cash_and_bank_balance": get_cash_and_bank_balance,
	"get_profit_and_loss_overview": get_profit_and_loss_overview,
	"get_expense_breakdown": get_expense_breakdown,
	"get_trial_balance_summary": get_trial_balance_summary,
	"get_tax_liability_overview": get_tax_liability_overview,
	"get_payment_entry_summary": get_payment_entry_summary,
}


def get_branch_profit_and_loss(date_from: str | None = None, date_to: str | None = None) -> dict:
	"""Income/expense/net profit per branch (GL Entry's own branch field), with a
	synthetic "Unassigned" bucket for rows with no branch tagged - real production
	data has many untagged GL rows (see CLAUDE.md's branch_management notes), so an
	Unassigned bucket is necessary rather than silently dropping those rows."""
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_from, date_to = _get_date_range(date_from, date_to)

	income_rows = frappe.db.sql(
		"""
        SELECT COALESCE(ge.branch, 'Unassigned') as branch, SUM(ge.credit - ge.debit) as amount
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND acc.is_group = 0 AND acc.root_type = 'Income'
        GROUP BY COALESCE(ge.branch, 'Unassigned')
        """,
		(company, date_from, date_to),
		as_dict=True,
	)
	expense_rows = frappe.db.sql(
		"""
        SELECT COALESCE(ge.branch, 'Unassigned') as branch, SUM(ge.debit - ge.credit) as amount
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %s AND ge.is_cancelled = 0
          AND ge.posting_date BETWEEN %s AND %s
          AND acc.is_group = 0 AND acc.root_type = 'Expense'
        GROUP BY COALESCE(ge.branch, 'Unassigned')
        """,
		(company, date_from, date_to),
		as_dict=True,
	)

	income_by_branch = {r["branch"]: flt(r["amount"]) for r in income_rows}
	expense_by_branch = {r["branch"]: flt(r["amount"]) for r in expense_rows}
	all_branches = set(income_by_branch.keys()) | set(expense_by_branch.keys())

	branches = []
	total_net_profit = 0.0
	for branch in sorted(all_branches):
		total_income = income_by_branch.get(branch, 0.0)
		total_expense = expense_by_branch.get(branch, 0.0)
		net_profit = total_income - total_expense
		net_margin_pct = (net_profit / total_income * 100) if total_income else 0.0
		branches.append(
			{
				"branch": branch,
				"total_income": total_income,
				"total_expense": total_expense,
				"net_profit": net_profit,
				"net_margin_pct": net_margin_pct,
			}
		)
		total_net_profit += net_profit

	branches.sort(key=lambda b: b["net_profit"], reverse=True)

	return {
		"company": company,
		"currency": currency,
		"date_from": date_from,
		"date_to": date_to,
		"branches": branches,
		"total_net_profit": total_net_profit,
	}


TOOL_SPECS.append(
	{
		"type": "function",
		"function": {
			"name": "get_branch_profit_and_loss",
			"description": "Get income, expense, and net profit broken down by branch (using GL Entry's branch field) with an Unassigned bucket for untagged rows. Use for questions about which branch is most/least profitable.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": 'Start date, YYYY-MM-DD. Defaults to date_to if omitted - resolve phrases like "this month" or "this year" into a concrete date yourself before calling.',
					},
					"date_to": {"type": "string", "description": "End date (YYYY-MM-DD), defaults to today."},
				},
			},
		},
	}
)

TOOL_DISPATCH["get_branch_profit_and_loss"] = get_branch_profit_and_loss
