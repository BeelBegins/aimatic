"""Certified customer RFM segmentation."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_days, cint, date_diff, flt, getdate

from aimatic.ai.tools import _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "customer-rfm-v1"


def score_rfm(rows: list[dict[str, Any]], as_of=None) -> list[dict[str, Any]]:
	as_of = getdate(as_of)
	if not rows:
		return []
	recencies = sorted(date_diff(as_of, getdate(row["last_purchase_date"])) for row in rows)
	frequencies = sorted(flt(row.get("frequency")) for row in rows)
	monetary = sorted(flt(row.get("monetary_value")) for row in rows)

	def quintile(value, ordered, reverse=False):
		if len(ordered) == 1:
			return 5
		rank = sum(candidate <= value for candidate in ordered) / len(ordered)
		score = max(1, min(5, int((rank - 1e-9) * 5) + 1))
		return 6 - score if reverse else score

	result = []
	for raw in rows:
		row = dict(raw)
		recency = date_diff(as_of, getdate(row["last_purchase_date"]))
		r_score = quintile(recency, recencies, reverse=True)
		f_score = quintile(flt(row.get("frequency")), frequencies)
		m_score = quintile(flt(row.get("monetary_value")), monetary)
		if r_score >= 4 and f_score >= 4 and m_score >= 4:
			segment, engagement = "Champions", "VIP retention and early access"
		elif r_score >= 3 and f_score >= 4:
			segment, engagement = "Loyal", "Loyalty reward and cross-sell"
		elif r_score >= 4 and f_score <= 2:
			segment, engagement = "New or Promising", "Second-purchase nurture"
		elif r_score <= 2 and (f_score >= 3 or m_score >= 3):
			segment, engagement = "At Risk", "Targeted win-back"
		elif r_score == 1 and f_score <= 2:
			segment, engagement = "Dormant", "Low-cost reactivation"
		else:
			segment, engagement = "Regular", "Relevant routine offers"
		churn_risk = "high" if r_score <= 2 else "medium" if r_score == 3 else "low"
		row.update(
			{
				"recency_days": recency,
				"recency_score": r_score,
				"frequency_score": f_score,
				"monetary_score": m_score,
				"rfm_score": f"{r_score}{f_score}{m_score}",
				"customer_segment": segment,
				"churn_risk": churn_risk,
				"recommended_engagement_category": engagement,
			}
		)
		result.append(row)
	return result


def get_customer_rfm_segments(
	branch: str | None = None,
	customer_group: str | None = None,
	history_days: int = 365,
	minimum_transactions: int = 1,
	limit: int = 200,
) -> dict:
	company = _resolve_company()
	branches = _resolve_branch_filter(company, branch)
	if branches is not None and not branches:
		return {"segments": [], "row_count": 0, "calculation_version": CALCULATION_VERSION}
	history_days = max(90, min(cint(history_days or 365), 1095))
	minimum_transactions = max(1, min(cint(minimum_transactions or 1), 100))
	limit = max(1, min(cint(limit or 200), 500))
	date_to = getdate()
	date_from = add_days(date_to, -history_days + 1)
	branch_clause = "AND COALESCE(pi.branch, pp.branch) = %(branch)s" if branch else ""
	customer_clause = "AND pi.customer_group = %(customer_group)s" if customer_group else ""
	rows = frappe.db.sql(
		f"""
		SELECT pi.customer, MAX(pi.customer_name) AS customer_name,
		       MAX(pi.customer_group) AS customer_group,
		       MAX(pi.posting_date) AS last_purchase_date,
		       COUNT(DISTINCT CASE WHEN pi.is_return = 0 THEN pi.name END) AS frequency,
		       SUM(pi.grand_total) AS monetary_value,
		       SUM(CASE WHEN pi.is_return = 1 THEN ABS(pi.grand_total) ELSE 0 END) AS return_value
		FROM `tabPOS Invoice` pi
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  {branch_clause} {customer_clause}
		GROUP BY pi.customer
		HAVING frequency >= %(minimum_transactions)s
		ORDER BY monetary_value DESC
		LIMIT %(limit)s
		""",
		{
			"company": company,
			"branch": branch,
			"customer_group": customer_group,
			"date_from": date_from,
			"date_to": date_to,
			"minimum_transactions": minimum_transactions,
			"limit": limit,
		},
		as_dict=True,
	)
	segments = score_rfm(rows, date_to)
	counts = {}
	for row in segments:
		counts[row["customer_segment"]] = counts.get(row["customer_segment"], 0) + 1
	return {
		"company": company,
		"branch": branch,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"segments": segments,
		"segment_counts": counts,
		"high_value_customers": [row for row in segments if row["monetary_score"] >= 4][:20],
		"dormant_customers": [row for row in segments if row["customer_segment"] == "Dormant"][:20],
		"row_count": len(segments),
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "get_customer_rfm_segments",
			"description": "Certified customer recency, frequency, and monetary segmentation with churn risk, high-value and dormant groups, and deterministic engagement categories.",
			"parameters": {
				"type": "object",
				"properties": {
					"branch": {"type": "string"},
					"customer_group": {"type": "string"},
					"history_days": {"type": "integer"},
					"minimum_transactions": {"type": "integer"},
					"limit": {"type": "integer"},
				},
			},
		},
	}
]

TOOL_DISPATCH = {"get_customer_rfm_segments": get_customer_rfm_segments}
