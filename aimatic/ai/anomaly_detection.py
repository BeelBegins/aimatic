"""Deterministic business anomaly detection."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import frappe
from frappe.utils import add_days, cint, flt, getdate

from aimatic.ai.tools import _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "business-anomaly-v1"


def detect_series_anomalies(
	rows: list[dict[str, Any]],
	metric: str,
	dimension_keys: tuple[str, ...] = ("branch",),
	minimum_periods: int = 7,
	z_threshold: float = 2.5,
):
	grouped = defaultdict(list)
	for row in rows:
		grouped[tuple(row.get(key) for key in dimension_keys)].append(dict(row))
	anomalies = []
	for dimension, values in grouped.items():
		values.sort(key=lambda row: str(row.get("period")))
		if len(values) <= minimum_periods:
			continue
		current = flt(values[-1].get(metric))
		history = [flt(row.get(metric)) for row in values[:-1]]
		mean = sum(history) / len(history)
		stddev = math.sqrt(sum((value - mean) ** 2 for value in history) / len(history))
		if stddev <= 0:
			continue
		z_score = (current - mean) / stddev
		if abs(z_score) < z_threshold:
			continue
		anomalies.append(
			{
				**{key: value for key, value in zip(dimension_keys, dimension)},
				"metric": metric,
				"period": values[-1].get("period"),
				"actual_value": round(current, 2),
				"expected_low": round(mean - z_threshold * stddev, 2),
				"expected_high": round(mean + z_threshold * stddev, 2),
				"expected_value": round(mean, 2),
				"variance": round(current - mean, 2),
				"z_score": round(z_score, 2),
				"severity": "critical" if abs(z_score) >= 4 else "warning",
				"data_source": {
					"expense_amount": "GL Entry",
					"supplier_price": "Purchase Receipt Item",
					"gross_margin": "POS Invoice and Stock Ledger Entry",
				}.get(metric, "Submitted POS Invoice"),
			}
		)
	return anomalies


def get_business_anomalies(
	branch: str | None = None,
	lookback_days: int = 60,
	z_threshold: float = 2.5,
	limit: int = 100,
) -> dict:
	company = _resolve_company()
	branches = _resolve_branch_filter(company, branch)
	if branches is not None and not branches:
		return {"anomalies": [], "row_count": 0, "calculation_version": CALCULATION_VERSION}
	lookback_days = max(14, min(cint(lookback_days or 60), 365))
	z_threshold = max(1.5, min(flt(z_threshold or 2.5), 5))
	limit = max(1, min(cint(limit or 100), 300))
	date_to = getdate()
	date_from = add_days(date_to, -lookback_days + 1)
	branch_clause = "AND COALESCE(pi.branch, pp.branch) = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
		SELECT pi.posting_date AS period,
		       COALESCE(pi.branch, pp.branch, 'Unassigned') AS branch,
		       SUM(pi.grand_total) AS net_sales,
		       SUM(CASE WHEN pi.is_return = 1 THEN ABS(pi.grand_total) ELSE 0 END) AS returns,
		       AVG(COALESCE(pi.additional_discount_percentage, 0)) AS discount_pct,
		       COUNT(*) AS transactions
		FROM `tabPOS Invoice` pi
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s {branch_clause}
		GROUP BY pi.posting_date, COALESCE(pi.branch, pp.branch, 'Unassigned')
		ORDER BY branch, period
		""",
		{"company": company, "branch": branch, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)
	cogs_rows = frappe.db.sql(
		"""
		SELECT sle.posting_date AS period, COALESCE(w.custom_branch, 'Unassigned') AS branch,
		       SUM(-sle.stock_value_difference) AS cogs
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabWarehouse` w ON w.name = sle.warehouse
		WHERE sle.is_cancelled = 0 AND sle.company = %(company)s
		  AND sle.voucher_type = 'POS Invoice'
		  AND sle.posting_date BETWEEN %(date_from)s AND %(date_to)s
		GROUP BY sle.posting_date, COALESCE(w.custom_branch, 'Unassigned')
		""",
		{"company": company, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)
	cogs_map = {(str(row.period), row.branch): flt(row.cogs) for row in cogs_rows}
	margin_rows = [
		{**dict(row), "gross_margin": flt(row.net_sales) - cogs_map[(str(row.period), row.branch)]}
		for row in rows
		if (str(row.period), row.branch) in cogs_map
	]
	expense_rows = frappe.db.sql(
		"""
		SELECT gle.posting_date AS period, 'Company-wide' AS branch,
		       SUM(gle.debit - gle.credit) AS expense_amount
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` a ON a.name = gle.account
		WHERE gle.is_cancelled = 0 AND gle.company = %(company)s
		  AND a.root_type = 'Expense'
		  AND gle.posting_date BETWEEN %(date_from)s AND %(date_to)s
		GROUP BY gle.posting_date
		ORDER BY period
		""",
		{"company": company, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)
	supplier_price_rows = frappe.db.sql(
		"""
		SELECT pr.posting_date AS period, pr.supplier,
		       AVG(COALESCE(NULLIF(pri.custom_price_after_taxes, 0), pri.rate)) AS supplier_price
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pr.docstatus = 1 AND IFNULL(pr.is_return, 0) = 0
		  AND pr.company = %(company)s
		  AND pr.posting_date BETWEEN %(date_from)s AND %(date_to)s
		GROUP BY pr.posting_date, pr.supplier
		ORDER BY pr.supplier, period
		""",
		{"company": company, "date_from": date_from, "date_to": date_to},
		as_dict=True,
	)
	anomalies = []
	for metric in ("net_sales", "returns", "discount_pct", "transactions"):
		anomalies.extend(detect_series_anomalies(rows, metric, ("branch",), 7, z_threshold))
	anomalies.extend(detect_series_anomalies(margin_rows, "gross_margin", ("branch",), 7, z_threshold))
	anomalies.extend(detect_series_anomalies(expense_rows, "expense_amount", ("branch",), 7, z_threshold))
	anomalies.extend(
		detect_series_anomalies(supplier_price_rows, "supplier_price", ("supplier",), 4, z_threshold)
	)
	negative_stock = frappe.db.sql(
		"""
		SELECT w.custom_branch AS branch, b.warehouse, b.item_code,
		       b.actual_qty AS actual_value
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		WHERE w.company = %(company)s AND b.actual_qty < 0
		ORDER BY b.actual_qty ASC
		LIMIT %(limit)s
		""",
		{"company": company, "limit": limit},
		as_dict=True,
	)
	for row in negative_stock:
		anomalies.append(
			{
				"branch": row.branch,
				"warehouse": row.warehouse,
				"item_code": row.item_code,
				"metric": "negative_stock",
				"actual_value": flt(row.actual_value),
				"expected_low": 0,
				"expected_high": None,
				"expected_value": 0,
				"variance": flt(row.actual_value),
				"severity": "critical",
				"data_source": "Bin",
			}
		)
	anomalies.sort(
		key=lambda row: (row["severity"] != "critical", -abs(flt(row.get("z_score") or row.get("variance"))))
	)
	return {
		"company": company,
		"branch": branch,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"anomalies": anomalies[:limit],
		"row_count": min(len(anomalies), limit),
		"total_anomalies": len(anomalies),
		"supporting_drill_down": {
			"doctype": "POS Invoice",
			"filters": {"company": company, "posting_date": ["between", [str(date_from), str(date_to)]]},
		},
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_business_anomalies",
			"description": "Deterministic sales-drop, return-spike, discount-spike, transaction-pattern, branch-performance, and negative-stock anomalies with expected ranges, severity, sources, and drill-down.",
			"parameters": {
				"type": "object",
				"properties": {
					"branch": {"type": "string"},
					"lookback_days": {"type": "integer"},
					"z_threshold": {"type": "number"},
					"limit": {"type": "integer"},
				},
			},
		},
	}
]

TOOL_DISPATCH = {"get_business_anomalies": get_business_anomalies}
