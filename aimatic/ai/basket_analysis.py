"""Certified bounded market-basket analysis."""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations

import frappe
from frappe.utils import add_days, cint, flt, getdate

from aimatic.ai.tools import _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "market-basket-v1"
_MAX_TRANSACTIONS = 5000
_MAX_ITEMS_PER_TRANSACTION = 30


def calculate_basket_pairs(
	transactions: dict[str, set[str]],
	minimum_transactions: int = 100,
	minimum_support: float = 0.01,
	minimum_confidence: float = 0.10,
	limit: int = 100,
):
	total = len(transactions)
	if total < minimum_transactions:
		return [], {"insufficient_data": True, "required_transactions": minimum_transactions}
	item_counts = Counter()
	pair_counts = Counter()
	for items in transactions.values():
		bounded = sorted(items)[:_MAX_ITEMS_PER_TRANSACTION]
		item_counts.update(bounded)
		pair_counts.update(combinations(bounded, 2))
	rows = []
	for (left, right), count in pair_counts.items():
		support = count / total
		confidence_left = count / item_counts[left]
		confidence_right = count / item_counts[right]
		confidence = max(confidence_left, confidence_right)
		lift = support / ((item_counts[left] / total) * (item_counts[right] / total))
		if support < minimum_support or confidence < minimum_confidence:
			continue
		rows.append(
			{
				"item_a": left,
				"item_b": right,
				"joint_transactions": count,
				"support": round(support * 100, 4),
				"confidence": round(confidence * 100, 4),
				"lift": round(lift, 4),
				"recommended_cross_sell": lift > 1 and confidence >= minimum_confidence,
			}
		)
	rows.sort(key=lambda row: (-row["lift"], -row["support"], row["item_a"], row["item_b"]))
	return rows[:limit], {"insufficient_data": False, "transactions": total}


def get_market_basket_analysis(
	branch: str | None = None,
	history_days: int = 90,
	minimum_transactions: int = 100,
	minimum_support: float = 1,
	minimum_confidence: float = 10,
	limit: int = 100,
) -> dict:
	company = _resolve_company()
	branches = _resolve_branch_filter(company, branch)
	if branches is not None and not branches:
		return {"pairs": [], "row_count": 0, "calculation_version": CALCULATION_VERSION}
	history_days = max(7, min(cint(history_days or 90), 365))
	minimum_transactions = max(20, min(cint(minimum_transactions or 100), _MAX_TRANSACTIONS))
	minimum_support = max(0.1, min(flt(minimum_support or 1), 50)) / 100
	minimum_confidence = max(1, min(flt(minimum_confidence or 10), 100)) / 100
	limit = max(1, min(cint(limit or 100), 500))
	date_to = getdate()
	date_from = add_days(date_to, -history_days + 1)
	branch_clause = "AND COALESCE(pi.branch, pp.branch) = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
		SELECT pi.name AS transaction_id, pii.item_code
		FROM `tabPOS Invoice` pi
		INNER JOIN (
		  SELECT name FROM `tabPOS Invoice`
		  WHERE docstatus = 1 AND is_return = 0 AND company = %(company)s
		    AND posting_date BETWEEN %(date_from)s AND %(date_to)s
		  ORDER BY posting_date DESC, posting_time DESC
		  LIMIT %(max_transactions)s
		) selected ON selected.name = pi.name
		INNER JOIN `tabPOS Invoice Item` pii ON pii.parent = pi.name
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		WHERE pi.docstatus = 1 AND pi.is_return = 0 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  AND pii.stock_qty > 0 {branch_clause}
		ORDER BY pi.name, pii.item_code
		""",
		{
			"company": company,
			"branch": branch,
			"date_from": date_from,
			"date_to": date_to,
			"max_transactions": _MAX_TRANSACTIONS,
		},
		as_dict=True,
	)
	transactions = defaultdict(set)
	for row in rows:
		transactions[row.transaction_id].add(row.item_code)
	pairs, quality = calculate_basket_pairs(
		transactions, minimum_transactions, minimum_support, minimum_confidence, limit
	)
	return {
		"company": company,
		"branch": branch,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"pairs": pairs,
		"transaction_count": len(transactions),
		"row_count": len(pairs),
		"data_quality_warnings": (
			[f"At least {minimum_transactions} transactions are required; only {len(transactions)} were available."]
			if quality["insufficient_data"]
			else []
		),
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [{
	"type": "function",
	"function": {
		"name": "get_market_basket_analysis",
		"description": "Certified bounded item-pair analysis with minimum transaction, support, and confidence guards; returns support, confidence, lift, branch, and review-only cross-sell combinations.",
		"parameters": {
			"type": "object",
			"properties": {
				"branch": {"type": "string"},
				"history_days": {"type": "integer"},
				"minimum_transactions": {"type": "integer"},
				"minimum_support": {"type": "number"},
				"minimum_confidence": {"type": "number"},
				"limit": {"type": "integer"},
			},
		},
	},
}]

TOOL_DISPATCH = {"get_market_basket_analysis": get_market_basket_analysis}
