"""Extended read-only aggregate-query tools for the Nemotron chat agent (api.py:ask).

These 5 tools supplement the core 11 in tools.py with deeper item-price history,
price-increase detection, dead-stock analysis, top customers, and receivables.
All tools are cheap SUM/GROUP BY aggregates with capped limits, Company/Branch-
permission scoped identically to tools.py/sales_dashboard/vendor_performance.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

from aimatic.ai.tools import (
	_branch_warehouses,
	_get_date_range,
	_resolve_branch_filter,
	_resolve_company,
)


def get_item_price_history(item_code: str, months: int = 12) -> dict:
	"""Purchase-cost history (PR+PI) and current selling prices for one item.
	Returns latest/min/max/avg cost, cost_change_pct, current mrp/branch price-list rates,
	average POS selling rate over the window, plus the fast Item snapshot fields."""
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("Item {0} does not exist").format(item_code))

	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_to = getdate(today())
	date_from = add_days(date_to, -(months * 30))

	# --- Purchase-cost side: PR + PI history for this item ---
	# Adapted from purchase_printing._fetch_purchase_history (same join/ordering)
	pr_rows = frappe.db.sql(
		"""
        SELECT
            child.item_code,
            child.rate,
            child.custom_price_after_taxes,
            parent.posting_date,
            parent.posting_time,
            parent.creation,
            parent.name,
            child.idx,
            parent.supplier
        FROM `tabPurchase Receipt Item` child
        INNER JOIN `tabPurchase Receipt` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1
          AND parent.company = %(company)s
          AND child.item_code = %(item_code)s
          AND parent.posting_date BETWEEN %(date_from)s AND %(date_to)s
        ORDER BY
            parent.posting_date DESC,
            IFNULL(parent.posting_time, '00:00:00') DESC,
            parent.creation DESC,
            parent.name DESC,
            child.idx DESC
        """,
		{"company": company, "item_code": item_code, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)

	pi_rows = frappe.db.sql(
		"""
        SELECT
            child.item_code,
            child.rate,
            child.custom_price_after_taxes,
            parent.posting_date,
            parent.posting_time,
            parent.creation,
            parent.name,
            child.idx,
            parent.supplier
        FROM `tabPurchase Invoice Item` child
        INNER JOIN `tabPurchase Invoice` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1
          AND IFNULL(parent.is_return, 0) = 0
          AND parent.company = %(company)s
          AND child.item_code = %(item_code)s
          AND parent.posting_date BETWEEN %(date_from)s AND %(date_to)s
        ORDER BY
            parent.posting_date DESC,
            IFNULL(parent.posting_time, '00:00:00') DESC,
            parent.creation DESC,
            parent.name DESC,
            child.idx DESC
        """,
		{"company": company, "item_code": item_code, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)

	# Merge and sort by date (newest first)
	all_cost_rows = sorted(
		pr_rows + pi_rows,
		key=lambda r: (
			r.posting_date or "",
			r.posting_time or "",
			r.creation or "",
			r.name or "",
			r.idx or 0,
		),
		reverse=True,
	)

	cost_history = []
	cost_values = []
	for row in all_cost_rows:
		price_after_taxes = flt(row.custom_price_after_taxes)
		rate = flt(row.rate)
		cost_val = price_after_taxes if price_after_taxes else rate
		if cost_val:
			cost_values.append(cost_val)
		cost_history.append(
			{
				"date": str(row.posting_date),
				"doc_type": "Purchase Receipt" if row in pr_rows else "Purchase Invoice",
				"doc_name": row.name,
				"supplier": row.supplier,
				"rate": rate,
				"price_after_taxes": price_after_taxes,
			}
		)

	# Compute cost stats
	latest_cost = cost_values[0] if cost_values else None
	min_cost = min(cost_values) if cost_values else None
	max_cost = max(cost_values) if cost_values else None
	avg_cost = sum(cost_values) / len(cost_values) if cost_values else None

	# cost_change_pct: latest vs previous distinct value
	cost_change_pct = None
	if len(cost_values) >= 2:
		prev_distinct = None
		for v in cost_values[1:]:
			if v != latest_cost:
				prev_distinct = v
				break
		if prev_distinct and prev_distinct != 0:
			cost_change_pct = round((latest_cost - prev_distinct) / prev_distinct * 100, 2)

	# --- Selling-price side: current Item.custom_mrp + branch Item Price rows + POS average rate ---
	# NOTE: there is no Item.custom_shelf_price field - shelf price only exists on
	# Purchase Receipt Item rows and propagates into branch-specific Item Price
	# records (Selling - <Branch> price lists), never onto Item itself. custom_mrp
	# is the one Item-level selling-side field that actually exists (see the
	# shelf-pricing skill). Current per-branch selling prices come from Item Price.
	item_doc = frappe.get_cached_value(
		"Item",
		item_code,
		["item_name", "custom_mrp", "custom_latest_price_incl_taxes", "custom_latest_price_source_date"],
		as_dict=True,
	)

	item_price_rows = frappe.get_all(
		"Item Price",
		filters={"item_code": item_code, "selling": 1},
		fields=["price_list", "price_list_rate", "modified"],
		order_by="modified desc",
		limit=10,
	)

	# Average POS selling rate over the window
	pos_avg_row = frappe.db.sql(
		"""
        SELECT COALESCE(AVG(pii.rate), 0) AS avg_rate
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pii.item_code = %(item_code)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
        """,
		{"company": company, "item_code": item_code, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)
	avg_pos_rate = flt(pos_avg_row[0].avg_rate) if pos_avg_row else 0

	return {
		"company": company,
		"currency": currency,
		"item_code": item_code,
		"item_name": item_doc.item_name if item_doc else item_code,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"purchase_cost_history": cost_history,
		"cost_stats": {
			"latest": latest_cost,
			"min": min_cost,
			"max": max_cost,
			"avg": round(avg_cost, 2) if avg_cost else None,
			"change_pct": cost_change_pct,
			"data_points": len(cost_values),
		},
		"selling_prices": {
			"current_mrp": flt(item_doc.custom_mrp) if item_doc else 0,
			"branch_price_list_rates": [
				{"price_list": r.price_list, "rate": flt(r.price_list_rate)} for r in item_price_rows
			],
			"avg_pos_rate_in_window": avg_pos_rate,
			"note": "No separate selling-price history ledger exists; current shelf price lives per-branch in Item Price rows (propagated by shelf_pricing on Purchase Receipt submit), not on Item itself. Average POS rate is computed from actual sales in the window.",
		},
		"fast_snapshot": {
			"latest_price_incl_taxes": flt(item_doc.custom_latest_price_incl_taxes) if item_doc else 0,
			"latest_price_source_date": str(item_doc.custom_latest_price_source_date)
			if item_doc and item_doc.custom_latest_price_source_date
			else None,
		},
	}


def get_price_increases(
	months: int = 3, min_change_pct: float = 10.0, branch: str | None = None, limit: int = 20
) -> dict:
	"""Items with 2+ purchase price points in the window where latest vs previous
	cost increased by >= min_change_pct. Returns top items by change_pct desc."""
	months = max(1, min(cint(months or 3), 24))
	min_change_pct = max(0.0, flt(min_change_pct or 10.0))
	limit = max(1, min(cint(limit or 20), 100))

	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	date_to = getdate(today())
	date_from = add_days(date_to, -(months * 30))

	branch_filter = _resolve_branch_filter(company, branch)
	if branch_filter is not None and not branch_filter:
		return {
			"company": company,
			"currency": currency,
			"date_from": str(date_from),
			"date_to": str(date_to),
			"items": [],
		}

	branch_clause = "AND parent.branch IN %(branch_names)s" if branch_filter is not None else ""
	params = {
		"company": company,
		"date_from": date_from,
		"date_to": date_to,
		"branch_names": tuple(branch_filter) if branch_filter else (),
	}

	# Single query pulling all recent PR+PI item rows in window, grouped by item_code
	pr_rows = frappe.db.sql(
		f"""
        SELECT
            child.item_code,
            child.custom_price_after_taxes,
            child.rate,
            parent.posting_date,
            parent.posting_time,
            parent.creation,
            parent.name,
            child.idx,
            parent.supplier
        FROM `tabPurchase Receipt Item` child
        INNER JOIN `tabPurchase Receipt` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1
          AND parent.company = %(company)s
          AND parent.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
		params,
		as_dict=True,
	)

	pi_rows = frappe.db.sql(
		f"""
        SELECT
            child.item_code,
            child.custom_price_after_taxes,
            child.rate,
            parent.posting_date,
            parent.posting_time,
            parent.creation,
            parent.name,
            child.idx,
            parent.supplier
        FROM `tabPurchase Invoice Item` child
        INNER JOIN `tabPurchase Invoice` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1
          AND IFNULL(parent.is_return, 0) = 0
          AND parent.company = %(company)s
          AND parent.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
		params,
		as_dict=True,
	)

	# Group by item_code, sort each group by date desc
	from collections import defaultdict

	by_item: dict[str, list] = defaultdict(list)
	for row in pr_rows + pi_rows:
		by_item[row.item_code].append(row)

	for item_code in by_item:
		by_item[item_code].sort(
			key=lambda r: (
				r.posting_date or "",
				r.posting_time or "",
				r.creation or "",
				r.name or "",
				r.idx or 0,
			),
			reverse=True,
		)

	# Compute latest vs previous for each item with 2+ points
	increases = []
	for item_code, rows in by_item.items():
		cost_vals = []
		latest_supplier = None
		latest_date = None
		for row in rows:
			price_after_taxes = flt(row.custom_price_after_taxes)
			rate = flt(row.rate)
			cost_val = price_after_taxes if price_after_taxes else rate
			if cost_val:
				cost_vals.append(cost_val)
				if latest_supplier is None:
					latest_supplier = row.supplier
					latest_date = row.posting_date
		if len(cost_vals) >= 2:
			latest = cost_vals[0]
			prev_distinct = None
			for v in cost_vals[1:]:
				if v != latest:
					prev_distinct = v
					break
			if prev_distinct and prev_distinct != 0:
				change_pct = round((latest - prev_distinct) / prev_distinct * 100, 2)
				if change_pct >= min_change_pct:
					item_name = frappe.get_cached_value("Item", item_code, "item_name") or item_code
					increases.append(
						{
							"item_code": item_code,
							"item_name": item_name,
							"supplier": latest_supplier,
							"old_price": prev_distinct,
							"new_price": latest,
							"change_pct": change_pct,
							"change_date": str(latest_date) if latest_date else None,
						}
					)

	increases.sort(key=lambda x: -x["change_pct"])
	return {
		"company": company,
		"currency": currency,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"min_change_pct": min_change_pct,
		"items": increases[:limit],
	}


def get_dead_stock_detail(
	min_days_since_last_sale: int = 180, branch: str | None = None, limit: int = 30
) -> dict:
	"""Items with current stock_value > 0 where last sale was >= min_days_since_last_sale
	days ago (or never sold). Returns total_dead_stock_value across ALL qualifying
	items (not just the returned page)."""
	min_days_since_last_sale = max(1, cint(min_days_since_last_sale or 180))
	limit = max(1, min(cint(limit or 30), 100))

	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")
	branch_filter = _resolve_branch_filter(company, branch)
	warehouse_filter = _branch_warehouses(branch_filter)

	if warehouse_filter is not None and not warehouse_filter:
		return {
			"company": company,
			"currency": currency,
			"min_days_since_last_sale": min_days_since_last_sale,
			"items": [],
			"total_dead_stock_value": 0,
		}

	warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
	sle_warehouse_clause = "AND warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
	params = {"company": company, "warehouse_names": tuple(warehouse_filter) if warehouse_filter else ()}

	# Current stock from Bin (stock_value > 0)
	stock_rows = frappe.db.sql(
		f"""
        SELECT b.item_code, i.item_name, SUM(b.actual_qty) AS stock_qty, SUM(b.stock_value) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE w.company = %(company)s {warehouse_clause}
        GROUP BY b.item_code, i.item_name
        HAVING SUM(b.stock_value) > 0.0001
        """,
		params,
		as_dict=True,
	)

	if not stock_rows:
		return {
			"company": company,
			"currency": currency,
			"min_days_since_last_sale": min_days_since_last_sale,
			"items": [],
			"total_dead_stock_value": 0,
		}

	item_codes = [r.item_code for r in stock_rows]
	item_codes_tuple = tuple(item_codes)

	# Last sale date from SLE (voucher_type IN Sales Invoice, POS Invoice, actual_qty < 0)
	last_sale_rows = frappe.db.sql(
		f"""
        SELECT item_code, MAX(posting_date) AS last_sale_date
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0 AND company = %(company)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice') AND actual_qty < 0
          AND item_code IN %(item_codes)s
          {sle_warehouse_clause}
        GROUP BY item_code
        """,
		{**params, "item_codes": item_codes_tuple},
		as_dict=True,
	)
	last_sale_by_item = {r.item_code: r.last_sale_date for r in last_sale_rows}

	today_date = getdate(today())
	results = []
	total_dead_value = 0.0

	for row in stock_rows:
		last_sale = last_sale_by_item.get(row.item_code)
		if last_sale:
			days_since = (today_date - getdate(last_sale)).days
		else:
			days_since = None  # never sold

		qualifies = (days_since is None) or (days_since >= min_days_since_last_sale)
		if qualifies:
			stock_value = flt(row.stock_value)
			total_dead_value += stock_value
			results.append(
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"stock_qty": flt(row.stock_qty),
					"stock_value": stock_value,
					"last_sale_date": str(last_sale) if last_sale else None,
					"days_since_last_sale": days_since,
				}
			)

	results.sort(key=lambda r: -r["stock_value"])
	return {
		"company": company,
		"currency": currency,
		"min_days_since_last_sale": min_days_since_last_sale,
		"items": results[:limit],
		"total_dead_stock_value": round(total_dead_value, 2),
	}


def get_top_customers(
	date_from: str | None = None,
	date_to: str | None = None,
	branch: str | None = None,
	order_by: str = "revenue",
	limit: int = 10,
) -> dict:
	"""Top customers by POS sales revenue or transaction count in a date range.
	Same query shape as get_top_selling_items but grouped by customer."""
	order_by = order_by if order_by in ("revenue", "frequency") else "revenue"
	limit = max(1, min(cint(limit or 10), 50))

	company = _resolve_company()
	date_from, date_to = _get_date_range(date_from, date_to)
	branch_filter = _resolve_branch_filter(company, branch)
	currency = frappe.get_cached_value("Company", company, "default_currency")

	if branch_filter is not None and not branch_filter:
		return {
			"company": company,
			"currency": currency,
			"date_from": str(date_from),
			"date_to": str(date_to),
			"order_by": order_by,
			"customers": [],
		}

	branch_clause = (
		"AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
	)
	order_column = "sales_amount" if order_by == "revenue" else "txn_count"
	params = {
		"company": company,
		"date_from": date_from,
		"date_to": date_to,
		"branch_names": tuple(branch_filter) if branch_filter else (),
		"limit": limit,
	}

	rows = frappe.db.sql(
		f"""
        SELECT pi.customer, COUNT(*) AS txn_count, COALESCE(SUM(pi.grand_total), 0) AS sales_amount
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY pi.customer
        ORDER BY {order_column} DESC
        LIMIT %(limit)s
        """,
		params,
		as_dict=True,
	)

	customers = []
	for row in rows:
		customer_name = frappe.get_cached_value("Customer", row.customer, "customer_name") or row.customer
		customers.append(
			{
				"customer": row.customer,
				"customer_name": customer_name,
				"txn_count": cint(row.txn_count),
				"sales_amount": flt(row.sales_amount),
			}
		)

	return {
		"company": company,
		"currency": currency,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"order_by": order_by,
		"customers": customers,
	}


def get_receivables_overview(limit: int = 10) -> dict:
	"""Total outstanding receivable from customers plus top N customers by amount owed.
	Mirrors get_outstanding_payables_overview exactly but for Sales Invoice/customer.
	Header-level outstanding_amount is not duplicated by POS consolidation (unlike
	item-level revenue), so Sales Invoice is the correct source."""
	limit = max(1, min(cint(limit or 10), 30))
	company = _resolve_company()
	currency = frappe.get_cached_value("Company", company, "default_currency")

	total_row = frappe.db.sql(
		"""
        SELECT COALESCE(SUM(outstanding_amount), 0) AS outstanding_amount, COUNT(*) AS invoice_count
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s AND outstanding_amount > 0
        """,
		{"company": company},
		as_dict=True,
	)[0]

	customer_rows = frappe.db.sql(
		"""
        SELECT customer, SUM(outstanding_amount) AS outstanding_amount, COUNT(*) AS invoice_count
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s AND outstanding_amount > 0
        GROUP BY customer
        ORDER BY outstanding_amount DESC
        LIMIT %(limit)s
        """,
		{"company": company, "limit": limit},
		as_dict=True,
	)

	top_customers = []
	for row in customer_rows:
		customer_name = frappe.get_cached_value("Customer", row.customer, "customer_name") or row.customer
		top_customers.append(
			{
				"customer": row.customer,
				"customer_name": customer_name,
				"outstanding_amount": flt(row.outstanding_amount),
				"invoice_count": cint(row.invoice_count),
			}
		)

	return {
		"company": company,
		"currency": currency,
		"total_outstanding_amount": flt(total_row.outstanding_amount),
		"total_outstanding_invoice_count": cint(total_row.invoice_count),
		"top_customers": top_customers,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_item_price_history",
			"description": "Get purchase-cost history (Purchase Receipt + Purchase Invoice) and current selling prices for a single item. Returns cost stats (latest/min/max/avg, change %), current MRP and per-branch Item Price rates, average POS selling rate over the window, and the fast Item snapshot fields (custom_latest_price_incl_taxes). Use for any question about an item's cost trend or current pricing.",
			"parameters": {
				"type": "object",
				"properties": {
					"item_code": {"type": "string", "description": "Exact Item code to look up."},
					"months": {
						"type": "integer",
						"description": "Lookback window in months for purchase history, default 12, max 24.",
					},
				},
				"required": ["item_code"],
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_price_increases",
			"description": "Find items where the latest purchase cost increased vs. the previous cost point by at least min_change_pct in the last N months. Returns items sorted by % increase descending. Use for 'which items/suppliers raised prices recently' questions.",
			"parameters": {
				"type": "object",
				"properties": {
					"months": {
						"type": "integer",
						"description": "Lookback window in months, default 3, max 24.",
					},
					"min_change_pct": {
						"type": "number",
						"description": "Minimum % increase to include, default 10.0.",
					},
					"branch": {
						"type": "string",
						"description": "Exact Branch name to scope to (filters PR/PI header branch). Omit for all branches the user can see.",
					},
					"limit": {"type": "integer", "description": "Max items to return, default 20, max 100."},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_dead_stock_detail",
			"description": "Get items with current stock value > 0 that haven't sold in at least min_days_since_last_sale days (or never sold). Returns each item's stock_qty, stock_value, last_sale_date, days_since_last_sale, plus total_dead_stock_value across ALL qualifying items (not just the returned page). Use for 'dead stock worth more than X' or 'slow moving inventory' questions.",
			"parameters": {
				"type": "object",
				"properties": {
					"min_days_since_last_sale": {
						"type": "integer",
						"description": "Minimum days since last sale to qualify as dead stock, default 180.",
					},
					"branch": {
						"type": "string",
						"description": "Exact Branch name to scope to. Omit for all branches the user can see.",
					},
					"limit": {"type": "integer", "description": "Max items to return, default 30, max 100."},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_top_customers",
			"description": "Get the top customers by POS sales revenue or transaction frequency for a date range. Use for any question about best/top/most valuable customers.",
			"parameters": {
				"type": "object",
				"properties": {
					"date_from": {
						"type": "string",
						"description": "Start date, YYYY-MM-DD. Defaults to today if omitted.",
					},
					"date_to": {
						"type": "string",
						"description": "End date, YYYY-MM-DD. Defaults to date_from if omitted.",
					},
					"branch": {
						"type": "string",
						"description": "Exact Branch name to scope to. Omit for all branches the user can see.",
					},
					"order_by": {
						"type": "string",
						"enum": ["revenue", "frequency"],
						"description": "Rank by sales revenue or by transaction count, default revenue.",
					},
					"limit": {
						"type": "integer",
						"description": "Max customers to return, default 10, max 50.",
					},
				},
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_receivables_overview",
			"description": "Get total outstanding receivable owed by customers and the top customers by amount owed. Use for any question about how much customers owe, or overdue/outstanding receivables.",
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
]

TOOL_DISPATCH = {
	"get_item_price_history": get_item_price_history,
	"get_price_increases": get_price_increases,
	"get_dead_stock_detail": get_dead_stock_detail,
	"get_top_customers": get_top_customers,
	"get_receivables_overview": get_receivables_overview,
}
