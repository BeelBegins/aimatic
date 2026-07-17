"""Rule-based business insight detectors that run after a tool-calling turn.

Each detector inspects the accumulated tool_results dict (keyed by exact tool
function name from tools.py) and emits zero or more Insight objects. All
detectors are pure functions with no side effects and no LLM calls.
"""

from __future__ import annotations

from aimatic.ai.response_schema import Insight


# ──────────────────────────────────────────────────────────────────────
# Private detector functions — one per rule
# ──────────────────────────────────────────────────────────────────────

def _detect_dead_stock(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 1: Dead stock. Prefers get_dead_stock_detail (actual last-sale-date
    based, more precise) when present; falls back to get_inventory_vs_sales'
    zero-sales-in-window ratio (added first, kept for when only that tool ran)."""
    dead_stock_res = tool_results.get("get_dead_stock_detail")
    if dead_stock_res:
        items = dead_stock_res.get("items", [])
        total_value = dead_stock_res.get("total_dead_stock_value", 0)
        item_count = len(items)
        if not items or (total_value <= 100_000 and item_count < 3):
            return []
        return [Insight(
            type="opportunity",
            severity="medium",
            title="Dead Stock Accumulating",
            description=(
                f"{item_count}+ items have not sold in at least "
                f"{dead_stock_res.get('min_days_since_last_sale', 180)} days (or ever), holding "
                f"{total_value:,.0f} {dead_stock_res.get('currency', '')} in stock value. "
                "Consider clearance promotions or supplier returns to free up cash."
            ),
            supporting_data={
                "total_stock_value": total_value,
                "item_count": item_count,
                "min_days_since_last_sale": dead_stock_res.get("min_days_since_last_sale"),
                "currency": dead_stock_res.get("currency"),
                "items": [
                    {"item_code": it["item_code"], "item_name": it["item_name"], "stock_value": it["stock_value"]}
                    for it in items[:10]
                ],
            },
            actionable=True,
        )]

    res = tool_results.get("get_inventory_vs_sales")
    if not res or res.get("direction") != "overstock":
        return []

    items = res.get("items", [])
    dead_items = [
        it for it in items
        if it.get("sales_qty_in_window", 0) == 0 and it.get("stock_value", 0) > 0
    ]
    if not dead_items:
        return []

    total_value = sum(it["stock_value"] for it in dead_items)
    item_count = len(dead_items)

    # Threshold: meaningfully large if total value > 100,000 OR at least 3 such items
    # (adjustable per business; documented here for transparency)
    if total_value <= 100_000 and item_count < 3:
        return []

    return [Insight(
        type="opportunity",
        severity="medium",
        title="Dead Stock Accumulating",
        description=(
            f"{item_count} items have zero sales in the last {res.get('lookback_days', 30)} days "
            f"but hold {total_value:,.0f} {res.get('currency', '')} in stock value. "
            "Consider clearance promotions or supplier returns to free up cash."
        ),
        supporting_data={
            "total_stock_value": total_value,
            "item_count": item_count,
            "lookback_days": res.get("lookback_days"),
            "currency": res.get("currency"),
            "items": [
                {"item_code": it["item_code"], "item_name": it["item_name"], "stock_value": it["stock_value"]}
                for it in dead_items[:10]  # top 10 for reference
            ],
        },
        actionable=True,
    )]


def _detect_price_increases(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 9: Purchase cost increases — items flagged by get_price_increases."""
    res = tool_results.get("get_price_increases")
    if not res:
        return []

    items = res.get("items", [])
    if not items:
        return []

    severity = "high" if any(it["change_pct"] >= 25 for it in items) else "medium"
    top_few = items[:5]
    top_few_desc = ", ".join(f"{it['item_name']} (+{it['change_pct']:.1f}%)" for it in top_few)

    return [Insight(
        type="risk",
        severity=severity,
        title="Purchase Cost Increases Detected",
        description=(
            f"{len(items)} items had a purchase cost increase of at least "
            f"{res.get('min_change_pct', 10)}% recently. Top: {top_few_desc}. "
            "Review whether selling prices still cover the new cost."
        ),
        supporting_data={
            "item_count": len(items),
            "min_change_pct": res.get("min_change_pct"),
            "items": [
                {"item_code": it["item_code"], "item_name": it["item_name"], "change_pct": it["change_pct"], "supplier": it["supplier"]}
                for it in top_few
            ],
        },
        actionable=True,
    )]


def _detect_stockout_risk(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 2: Stockout risk — understock items with days_of_stock < 7."""
    res = tool_results.get("get_inventory_vs_sales")
    if not res or res.get("direction") != "understock":
        return []

    items = res.get("items", [])
    at_risk = [
        it for it in items
        if it.get("days_of_stock") is not None and it["days_of_stock"] < 7
    ]
    if not at_risk:
        return []

    # Sort by lowest days_of_stock first
    at_risk.sort(key=lambda x: x["days_of_stock"])
    top_few = at_risk[:5]
    top_few_desc = ", ".join(f"{it['item_name']} ({it['days_of_stock']:.1f} days)" for it in top_few)

    return [Insight(
        type="risk",
        severity="high",
        title="Imminent Stockout Risk",
        description=(
            f"{len(at_risk)} items have fewer than 7 days of stock remaining. "
            f"Most critical: {top_few_desc}."
        ),
        supporting_data={
            "at_risk_count": len(at_risk),
            "lookback_days": res.get("lookback_days"),
            "top_items": [
                {"item_code": it["item_code"], "item_name": it["item_name"], "days_of_stock": it["days_of_stock"], "stock_qty": it["stock_qty"]}
                for it in top_few
            ],
        },
        actionable=True,
    )]


def _detect_negative_vendor_margin(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 3: Negative/weak vendor margin — vendors with gross_margin_pct < 10%."""
    res = tool_results.get("rank_vendors")
    if not res:
        return []

    vendors = res.get("vendors", [])
    weak_vendors = [
        v for v in vendors
        if v.get("gross_margin_pct") is not None and v["gross_margin_pct"] < 10
    ]
    if not weak_vendors:
        return []

    # Severity: critical if any margin < 0, else medium
    has_negative = any(v["gross_margin_pct"] < 0 for v in weak_vendors)
    severity = "critical" if has_negative else "medium"

    return [Insight(
        type="risk",
        severity=severity,
        title="Weak or Negative Vendor Margins",
        description=(
            f"{len(weak_vendors)} suppliers have gross margin below 10% "
            f"({'including negative margins' if has_negative else 'all positive but thin'}). "
            "Review purchase pricing or sales pricing for these vendors."
        ),
        supporting_data={
            "vendor_count": len(weak_vendors),
            "vendors": [
                {"supplier": v["supplier"], "gross_margin_pct": v["gross_margin_pct"], "purchase_amount": v["purchase_amount"], "sales_amount": v["sales_amount"]}
                for v in weak_vendors[:10]
            ],
        },
        actionable=True,
    )]


def _detect_branch_underperformance(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 4: Branch underperformance — branches below 50% of mean net_sales."""
    # Prefer get_branch_comparison; fall back to get_sales_overview.branch_breakdown
    res = tool_results.get("get_branch_comparison") or tool_results.get("get_sales_overview")
    if not res:
        return []

    branches = res.get("branches") or res.get("branch_breakdown", [])
    if len(branches) < 3:
        return []  # not enough for meaningful comparison

    sales_values = [b.get("net_sales", 0) for b in branches]
    mean_sales = sum(sales_values) / len(sales_values)
    threshold = mean_sales * 0.5

    underperformers = [
        b for b in branches
        if b.get("net_sales", 0) < threshold
    ]
    if not underperformers:
        return []

    underperformers_desc = ", ".join(f"{b['branch']} ({b['net_sales']:,.0f})" for b in underperformers)

    return [Insight(
        type="anomaly",
        severity="medium",
        title="Branch Sales Underperformance",
        description=(
            f"{len(underperformers)} of {len(branches)} branches are below 50% of the mean "
            f"net sales ({mean_sales:,.0f} {res.get('currency', '')}). "
            f"Underperformers: {underperformers_desc}."
        ),
        supporting_data={
            "branch_count": len(branches),
            "mean_net_sales": mean_sales,
            "threshold": threshold,
            "currency": res.get("currency"),
            "underperformers": [
                {"branch": b["branch"], "net_sales": b["net_sales"], "pct_of_mean": round(b["net_sales"] / mean_sales * 100, 1)}
                for b in underperformers
            ],
        },
        actionable=True,
    )]


def _detect_low_gross_margin(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 5: Low gross margin — company-wide margin < 15% with sales > 0."""
    res = tool_results.get("get_gross_margin_overview")
    if not res:
        return []

    sales_amount = res.get("sales_amount", 0)
    margin_pct = res.get("gross_margin_pct", 0)

    if sales_amount <= 0 or margin_pct >= 15:
        return []

    # Severity based on how far below 15%
    if margin_pct < 5:
        severity = "critical"
    elif margin_pct < 10:
        severity = "high"
    else:
        severity = "medium"

    return [Insight(
        type="risk",
        severity=severity,
        title="Low Gross Margin",
        description=(
            f"Company-wide gross margin is {margin_pct:.1f}% on {sales_amount:,.0f} "
            f"{res.get('currency', '')} sales — well below the 15% healthy threshold. "
            "Investigate COGS increases or pricing pressure."
        ),
        supporting_data={
            "sales_amount": sales_amount,
            "cogs_amount": res.get("cogs_amount"),
            "gross_margin_amount": res.get("gross_margin_amount"),
            "gross_margin_pct": margin_pct,
            "currency": res.get("currency"),
            "threshold_pct": 15,
        },
        actionable=True,
    )]


def _detect_payables_concentration(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 6: Payables concentration — top supplier > 50% of total outstanding."""
    res = tool_results.get("get_outstanding_payables_overview")
    if not res:
        return []

    total = res.get("total_outstanding_amount", 0)
    top_suppliers = res.get("top_suppliers", [])

    if total <= 0 or not top_suppliers:
        return []

    top = top_suppliers[0]
    top_amount = top.get("outstanding_amount", 0)

    if top_amount / total <= 0.5:
        return []

    return [Insight(
        type="anomaly",
        severity="medium",
        title="Vendor Payables Concentration Risk",
        description=(
            f"Top supplier '{top['supplier']}' accounts for {top_amount / total * 100:.1f}% "
            f"of total outstanding payables ({total:,.0f} {res.get('currency', '')}). "
            "High concentration increases supply-chain and negotiation risk."
        ),
        supporting_data={
            "total_outstanding_amount": total,
            "top_supplier": top["supplier"],
            "top_supplier_amount": top_amount,
            "concentration_pct": round(top_amount / total * 100, 1),
            "currency": res.get("currency"),
        },
        actionable=True,
    )]


def _detect_high_return_rate(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 7: High return rate — needs both returns and sales overviews."""
    returns_res = tool_results.get("get_returns_overview")
    sales_res = tool_results.get("get_sales_overview")
    if not returns_res or not sales_res:
        return []

    returns_amount = returns_res.get("returns_amount", 0)
    gross_sales = sales_res.get("gross_sales", 0)

    if gross_sales <= 0:
        return []

    # Return rate = returns / (returns + gross_sales) — i.e., returns as % of total gross flow
    total_gross_flow = returns_amount + gross_sales
    return_rate = returns_amount / total_gross_flow if total_gross_flow > 0 else 0

    if return_rate <= 0.05:  # 5% threshold
        return []

    return [Insight(
        type="risk",
        severity="high" if return_rate > 0.10 else "medium",
        title="Elevated Return Rate",
        description=(
            f"Return rate is {return_rate * 100:.1f}% "
            f"({returns_amount:,.0f} returns vs {gross_sales:,.0f} gross sales). "
            "Investigate product quality, fulfillment accuracy, or policy abuse."
        ),
        supporting_data={
            "returns_amount": returns_amount,
            "gross_sales": gross_sales,
            "return_rate_pct": round(return_rate * 100, 2),
            "currency": sales_res.get("currency"),
        },
        actionable=True,
    )]


def _detect_positive_margin_signal(tool_results: dict[str, dict]) -> list[Insight]:
    """Rule 8: Positive signal — healthy gross margin >= 25%."""
    res = tool_results.get("get_gross_margin_overview")
    if not res:
        return []

    margin_pct = res.get("gross_margin_pct", 0)
    sales_amount = res.get("sales_amount", 0)

    if sales_amount <= 0 or margin_pct < 25:
        return []

    return [Insight(
        type="positive",
        severity="low",
        title="Healthy Gross Margin",
        description=(
            f"Gross margin is {margin_pct:.1f}% on {sales_amount:,.0f} "
            f"{res.get('currency', '')} sales — above the 25% healthy benchmark. "
            "Pricing and cost structure are performing well."
        ),
        supporting_data={
            "sales_amount": sales_amount,
            "cogs_amount": res.get("cogs_amount"),
            "gross_margin_amount": res.get("gross_margin_amount"),
            "gross_margin_pct": margin_pct,
            "currency": res.get("currency"),
            "benchmark_pct": 25,
        },
        actionable=False,
    )]


# ──────────────────────────────────────────────────────────────────────
# Public entrypoint
# ──────────────────────────────────────────────────────────────────────

# Ordered list of detector functions — order doesn't matter for results,
# but keeping a stable list makes testing predictable.
_DETECTORS = [
    _detect_dead_stock,
    _detect_stockout_risk,
    _detect_negative_vendor_margin,
    _detect_branch_underperformance,
    _detect_low_gross_margin,
    _detect_payables_concentration,
    _detect_high_return_rate,
    _detect_positive_margin_signal,
    _detect_price_increases,
]

# Severity sort order: critical > high > medium > low
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_MAX_INSIGHTS = 8  # cap to avoid overwhelming the answer


def generate_insights(tool_results: dict[str, dict]) -> list[Insight]:
    """Run all rule-based detectors over the accumulated tool results for one turn.

    Args:
        tool_results: Mapping of exact tool function name (from tools.py) to its
            returned dict. Keys may be missing if the model didn't call that tool.

    Returns:
        List of Insight objects, sorted by severity (critical/high first),
        capped at _MAX_INSIGHTS items.
    """
    all_insights: list[Insight] = []
    for detector in _DETECTORS:
        try:
            all_insights.extend(detector(tool_results))
        except Exception:
            # Detectors must never raise; log and continue in production.
            # Here we silently skip to keep the function pure and resilient.
            pass

    # Sort by severity (critical first), then by type for stability
    all_insights.sort(key=lambda i: (_SEVERITY_RANK.get(i.severity, 99), i.type))

    return all_insights[:_MAX_INSIGHTS]
