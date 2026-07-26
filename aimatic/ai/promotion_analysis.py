"""Certified promotion-effectiveness analysis from submitted POS transactions."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_days, cint, date_diff, flt, getdate

from aimatic.ai.tools import _resolve_branch_filter, _resolve_company

CALCULATION_VERSION = "promotion-effectiveness-v1"


def calculate_promotion_effect(
	baseline: dict[str, Any],
	promotion: dict[str, Any],
	post: dict[str, Any],
	promotion_days: int,
) -> dict[str, Any]:
	promotion_days = max(cint(promotion_days), 1)
	baseline_days = max(cint(baseline.get("days")), 1)
	post_days = max(cint(post.get("days")), 1)
	baseline_units = flt(baseline.get("quantity")) / baseline_days * promotion_days
	baseline_revenue = flt(baseline.get("revenue")) / baseline_days * promotion_days
	promo_units = flt(promotion.get("quantity"))
	promo_revenue = flt(promotion.get("revenue"))
	incremental_units = promo_units - baseline_units
	incremental_revenue = promo_revenue - baseline_revenue
	margin_available = baseline.get("margin") is not None and promotion.get("margin") is not None
	baseline_margin = flt(baseline.get("margin")) if margin_available else None
	promo_margin = flt(promotion.get("margin")) if margin_available else None
	discount_cost = max(
		flt(promotion.get("gross_before_discount")) - promo_revenue, 0
	)
	incremental_margin = (
		promo_margin - baseline_margin / baseline_days * promotion_days
		if margin_available
		else None
	)
	roi = incremental_margin / discount_cost * 100 if discount_cost > 0 and incremental_margin is not None else None
	post_daily = flt(post.get("quantity")) / post_days
	baseline_daily = flt(baseline.get("quantity")) / baseline_days
	post_drop = (post_daily - baseline_daily) / baseline_daily * 100 if baseline_daily else None
	category_baseline = flt(baseline.get("category_other_revenue"))
	category_promo = flt(promotion.get("category_other_revenue"))
	expected_other = category_baseline / baseline_days * promotion_days
	cannibalization = max(expected_other - category_promo, 0)
	return {
		"baseline_sales_quantity": round(baseline_units, 4),
		"baseline_sales": round(baseline_revenue, 2),
		"promotional_sales_quantity": round(promo_units, 4),
		"promotional_sales": round(promo_revenue, 2),
		"incremental_units": round(incremental_units, 4),
		"incremental_revenue": round(incremental_revenue, 2),
		"margin_before_promotion": round(baseline_margin, 2) if baseline_margin is not None else None,
		"margin_during_promotion": round(promo_margin, 2) if promo_margin is not None else None,
		"incremental_margin": round(incremental_margin, 2) if incremental_margin is not None else None,
		"discount_cost": round(discount_cost, 2),
		"cannibalization": round(cannibalization, 2),
		"post_promotion_drop_pct": round(post_drop, 2) if post_drop is not None else None,
		"promotion_roi_pct": round(roi, 2) if roi is not None else None,
	}


def get_promotion_effectiveness(
	item_code: str,
	promotion_from: str,
	promotion_to: str,
	branch: str | None = None,
	baseline_days: int = 28,
	post_days: int = 14,
) -> dict:
	if not item_code or not promotion_from or not promotion_to:
		return {"error": "item_code, promotion_from, and promotion_to are required."}
	company = _resolve_company()
	branches = _resolve_branch_filter(company, branch)
	if branches is not None and not branches:
		return {"error": "The selected branch is outside the visible scope."}
	promotion_from, promotion_to = getdate(promotion_from), getdate(promotion_to)
	if promotion_from > promotion_to:
		return {"error": "promotion_from must not be after promotion_to."}
	promotion_days = date_diff(promotion_to, promotion_from) + 1
	if promotion_days > 93:
		return {"error": "Promotion windows are capped at 93 days."}
	baseline_days = max(7, min(cint(baseline_days or 28), 90))
	post_days = max(7, min(cint(post_days or 14), 60))
	baseline_from = add_days(promotion_from, -baseline_days)
	baseline_to = add_days(promotion_from, -1)
	post_from = add_days(promotion_to, 1)
	post_to = add_days(promotion_to, post_days)
	item_group = frappe.get_cached_value("Item", item_code, "item_group")
	if not item_group:
		return {"error": "Item was not found."}
	branch_clause = "AND COALESCE(pi.branch, pp.branch, w.custom_branch) = %(branch)s" if branch else ""
	allocated = (
		"CASE WHEN ABS(IFNULL(pi.base_net_total, 0)) > 0 "
		"THEN (pii.base_net_amount / pi.base_net_total) * pi.grand_total "
		"ELSE pii.base_net_amount END"
	)
	rows = frappe.db.sql(
		f"""
		SELECT
		  CASE
		    WHEN pi.posting_date BETWEEN %(baseline_from)s AND %(baseline_to)s THEN 'baseline'
		    WHEN pi.posting_date BETWEEN %(promotion_from)s AND %(promotion_to)s THEN 'promotion'
		    WHEN pi.posting_date BETWEEN %(post_from)s AND %(post_to)s THEN 'post'
		  END AS period_role,
		  SUM(CASE WHEN pii.item_code = %(item_code)s THEN pii.stock_qty ELSE 0 END) AS quantity,
		  SUM(CASE WHEN pii.item_code = %(item_code)s THEN {allocated} ELSE 0 END) AS revenue,
		  SUM(CASE WHEN pii.item_code = %(item_code)s THEN pii.qty * pii.price_list_rate ELSE 0 END) AS gross_before_discount,
		  NULL AS margin,
		  SUM(CASE WHEN i.item_group = %(item_group)s AND pii.item_code != %(item_code)s THEN {allocated} ELSE 0 END) AS category_other_revenue,
		  COUNT(DISTINCT pi.name) AS transaction_count
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
		INNER JOIN `tabItem` i ON i.name = pii.item_code
		LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
		LEFT JOIN `tabWarehouse` w ON w.name = pii.warehouse
		WHERE pi.docstatus = 1 AND pi.company = %(company)s
		  AND pi.posting_date BETWEEN %(baseline_from)s AND %(post_to)s
		  {branch_clause}
		GROUP BY period_role
		""",
		{
			"company": company,
			"item_code": item_code,
			"item_group": item_group,
			"branch": branch,
			"baseline_from": baseline_from,
			"baseline_to": baseline_to,
			"promotion_from": promotion_from,
			"promotion_to": promotion_to,
			"post_from": post_from,
			"post_to": post_to,
		},
		as_dict=True,
	)
	periods = {row.period_role: dict(row) for row in rows if row.period_role}
	cogs_rows = frappe.db.sql(
		"""
		SELECT CASE
		         WHEN posting_date BETWEEN %(baseline_from)s AND %(baseline_to)s THEN 'baseline'
		         WHEN posting_date BETWEEN %(promotion_from)s AND %(promotion_to)s THEN 'promotion'
		         WHEN posting_date BETWEEN %(post_from)s AND %(post_to)s THEN 'post'
		       END AS period_role,
		       SUM(-stock_value_difference) AS cogs
		FROM `tabStock Ledger Entry`
		WHERE is_cancelled = 0 AND company = %(company)s
		  AND voucher_type = 'POS Invoice' AND item_code = %(item_code)s
		  AND posting_date BETWEEN %(baseline_from)s AND %(post_to)s
		GROUP BY period_role
		""",
		{
			"company": company, "item_code": item_code,
			"baseline_from": baseline_from, "baseline_to": baseline_to,
			"promotion_from": promotion_from, "promotion_to": promotion_to,
			"post_from": post_from, "post_to": post_to,
		},
		as_dict=True,
	)
	for cogs_row in cogs_rows:
		if cogs_row.period_role in periods:
			periods[cogs_row.period_role]["margin"] = flt(periods[cogs_row.period_role].get("revenue")) - flt(cogs_row.cogs)
	baseline = periods.get("baseline", {})
	baseline["days"] = baseline_days
	promotion = periods.get("promotion", {})
	post = periods.get("post", {})
	post["days"] = post_days
	metrics = calculate_promotion_effect(baseline, promotion, post, promotion_days)
	warnings = []
	if cint(baseline.get("transaction_count")) < 10:
		warnings.append("Baseline has fewer than 10 transactions.")
	if baseline.get("margin") is None or promotion.get("margin") is None:
		warnings.append("Certified Stock Ledger cost is unavailable for one or more periods; margin and ROI are not shown.")
	if flt(promotion.get("gross_before_discount")) <= flt(promotion.get("revenue")):
		warnings.append("No measurable item-level discount was found in the promotion window.")
	return {
		"company": company,
		"branch": branch,
		"item_code": item_code,
		"item_group": item_group,
		"baseline_period": {"from": str(baseline_from), "to": str(baseline_to)},
		"promotion_period": {"from": str(promotion_from), "to": str(promotion_to)},
		"post_period": {"from": str(post_from), "to": str(post_to)},
		**metrics,
		"data_quality_warnings": warnings,
		"rows_analyzed": sum(cint(row.transaction_count) for row in rows),
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [{
	"type": "function",
	"function": {
		"name": "get_promotion_effectiveness",
		"description": "Certified baseline-versus-promotion analysis with incremental units, revenue, margin, cannibalization, post-promotion drop, ROI, and source periods.",
		"parameters": {
			"type": "object",
			"properties": {
				"item_code": {"type": "string"},
				"promotion_from": {"type": "string"},
				"promotion_to": {"type": "string"},
				"branch": {"type": "string"},
				"baseline_days": {"type": "integer"},
				"post_days": {"type": "integer"},
			},
			"required": ["item_code", "promotion_from", "promotion_to"],
		},
	},
}]

TOOL_DISPATCH = {"get_promotion_effectiveness": get_promotion_effectiveness}
