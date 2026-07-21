"""Deterministic chart recommendation per tool result - no LLM, no guessing.

Given a tool name and its already-executed result dict, returns a single
response_schema.Chart or None. Each tool's output shape is known exactly from
tools.py, so logic is per-tool and explicit.
"""

from __future__ import annotations

from frappe.utils import flt

from aimatic.ai.response_schema import Chart, ChartData, ChartOptions

# Bar charts stay readable up to about this many bars before labels get
# truncated illegibly - tools like get_dead_stock_detail/get_price_increases
# can return up to 100 rows for their table, but the chart only ever shows
# the top N (already sorted by relevance by the tool itself).
_MAX_CHART_BARS = 10


def _chart_get_sales_overview(result: dict) -> Chart | None:
    """Horizontal bar of net_sales by branch from branch_breakdown."""
    breakdown = (result.get("branch_breakdown") or [])[:_MAX_CHART_BARS]
    if len(breakdown) < 2:
        return None
    labels = [row["branch"] for row in breakdown]
    data = [row["net_sales"] for row in breakdown]
    currency = result.get("currency", "PKR")
    return Chart(
        id="chart_sales_by_branch",
        title="Net Sales by Branch",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Net Sales", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_top_selling_items(result: dict) -> Chart | None:
    """Horizontal bar of sales_amount or sales_qty by item from items."""
    items = result.get("items") or []
    if not items:
        return None
    order_by = result.get("order_by", "revenue")
    if order_by == "quantity":
        labels = [row["item_name"] for row in items]
        data = [row["sales_qty"] for row in items]
        label = "Quantity Sold"
        fmt = "qty"
    else:
        labels = [row["item_name"] for row in items]
        data = [row["sales_amount"] for row in items]
        label = "Sales Revenue"
        fmt = "currency"
    return Chart(
        id="chart_top_selling_items",
        title=f"Top Selling Items by {'Quantity' if order_by == 'quantity' else 'Revenue'}",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": label, "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": fmt}),
        auto_selected=True,
    )


def _chart_get_inventory_vs_sales(result: dict) -> Chart | None:
    """Horizontal bar of days_of_stock for overstock direction only."""
    direction = result.get("direction", "overstock")
    if direction != "overstock":
        return None
    items = result.get("items") or []
    # Filter to items with valid days_of_stock (non-None)
    valid = [row for row in items if row.get("days_of_stock") is not None]
    if len(valid) < 2:
        return None
    labels = [row["item_name"] for row in valid]
    data = [row["days_of_stock"] for row in valid]
    return Chart(
        id="chart_overstock_days",
        title="Overstock Items — Days of Stock",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Days of Stock", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "number"}),
        auto_selected=True,
    )


def _chart_get_payment_mode_split(result: dict) -> Chart | None:
    """Donut chart of payment mode amounts."""
    split = result.get("payment_split") or []
    if not split:
        return None
    labels = [row["mode_of_payment"] for row in split]
    data = [row["amount"] for row in split]
    return Chart(
        id="chart_payment_mode_split",
        title="Payment Mode Breakdown",
        type="donut",
        data=ChartData(labels=labels, datasets=[{"label": "Amount", "data": data}]),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_returns_overview(result: dict) -> Chart | None:
    """Horizontal bar of returns_amount by branch from branch_breakdown."""
    breakdown = result.get("branch_breakdown") or []
    if len(breakdown) < 2:
        return None
    labels = [row["branch"] for row in breakdown]
    data = [row["returns_amount"] for row in breakdown]
    return Chart(
        id="chart_returns_by_branch",
        title="Returns by Branch",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Returns Amount", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_branch_comparison(result: dict) -> Chart | None:
    """Horizontal bar of net_sales by branch from branches."""
    branches = result.get("branches") or []
    if len(branches) < 2:
        return None
    labels = [row["branch"] for row in branches]
    data = [row["net_sales"] for row in branches]
    return Chart(
        id="chart_branch_comparison",
        title="Branch Sales Comparison",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Net Sales", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_rank_vendors(result: dict) -> Chart | None:
    """Horizontal bar of gross_margin_pct (skip None) or purchase_amount by vendor."""
    vendors = result.get("vendors") or []
    if not vendors:
        return None
    order = result.get("order", "best")
    if order == "best":
        # Chart gross_margin_pct, skip vendors where it's None
        valid = [v for v in vendors if v.get("gross_margin_pct") is not None]
        if len(valid) < 2:
            return None
        labels = [v["supplier"] for v in valid]
        data = [v["gross_margin_pct"] for v in valid]
        label = "Gross Margin %"
        fmt = "percent"
        title = "Vendor Gross Margin % (Best First)"
    else:
        # Chart purchase_amount for worst (by outstanding/margin)
        valid = [v for v in vendors if v.get("purchase_amount") is not None]
        if len(valid) < 2:
            return None
        labels = [v["supplier"] for v in valid]
        data = [v["purchase_amount"] for v in valid]
        label = "Purchase Amount"
        fmt = "currency"
        title = "Vendor Purchase Amount (Worst First)"
    return Chart(
        id="chart_vendor_ranking",
        title=title,
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": label, "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": fmt}),
        auto_selected=True,
    )


# Tools that never produce a chart (single-value or small-list results)
def _chart_get_purchase_overview(result: dict) -> None:
    return None


def _chart_get_active_shifts(result: dict) -> None:
    return None


def _chart_get_outstanding_payables_overview(result: dict) -> None:
    return None


def _chart_get_gross_margin_overview(result: dict) -> None:
    return None


def _chart_get_dead_stock_detail(result: dict) -> Chart | None:
    """Horizontal bar of stock_value by item, for the top dead-stock items."""
    items = (result.get("items") or [])[:_MAX_CHART_BARS]
    if len(items) < 2:
        return None
    labels = [row["item_name"] for row in items]
    data = [row["stock_value"] for row in items]
    return Chart(
        id="chart_dead_stock",
        title="Dead Stock Value by Item",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Stock Value", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_price_increases(result: dict) -> Chart | None:
    """Horizontal bar of change_pct by item, for items with a purchase-cost increase."""
    items = (result.get("items") or [])[:_MAX_CHART_BARS]
    if len(items) < 2:
        return None
    labels = [row["item_name"] for row in items]
    data = [row["change_pct"] for row in items]
    return Chart(
        id="chart_price_increases",
        title="Purchase Cost Increases by Item",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Cost Change %", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "percent"}),
        auto_selected=True,
    )


def _chart_get_top_customers(result: dict) -> Chart | None:
    """Horizontal bar of sales_amount or txn_count by customer."""
    customers = (result.get("customers") or [])[:_MAX_CHART_BARS]
    if len(customers) < 2:
        return None
    order_by = result.get("order_by", "revenue")
    if order_by == "frequency":
        labels = [row["customer_name"] for row in customers]
        data = [row["txn_count"] for row in customers]
        label = "Transactions"
        fmt = "number"
    else:
        labels = [row["customer_name"] for row in customers]
        data = [row["sales_amount"] for row in customers]
        label = "Sales Revenue"
        fmt = "currency"
    return Chart(
        id="chart_top_customers",
        title=f"Top Customers by {'Frequency' if order_by == 'frequency' else 'Revenue'}",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": label, "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": fmt}),
        auto_selected=True,
    )


def _chart_get_item_price_history(result: dict) -> Chart | None:
    """Line chart of purchase cost over successive purchase events for one item."""
    history = result.get("purchase_cost_history") or []
    if len(history) < 2:
        return None
    # History is newest-first; chart chronologically (oldest first).
    ordered = list(reversed(history))
    labels = [row["date"] for row in ordered]
    data = [row["price_after_taxes"] or row["rate"] for row in ordered]
    return Chart(
        id="chart_item_price_history",
        title=f"Purchase Cost Trend — {result.get('item_name', result.get('item_code', ''))}",
        type="line",
        data=ChartData(labels=labels, datasets=[{"label": "Purchase Cost", "data": data}]),
        options=ChartOptions(yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_receivables_overview(result: dict) -> None:
    return None


def _chart_get_payables_aging(result: dict) -> Chart | None:
    buckets = result.get("buckets") or {}
    if not buckets:
        return None
    labels = ["0-30", "31-60", "61-90", "90+"]
    values = [flt(buckets.get(l, 0)) for l in labels]
    if all(v == 0 for v in values):
        return None
    return Chart(
        id="chart_get_payables_aging",
        type="bar",
        title="Payables Aging Buckets",
        data=ChartData(
            labels=labels,
            datasets=[{"label": "Outstanding", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_receivables_aging(result: dict) -> Chart | None:
    buckets = result.get("buckets") or {}
    if not buckets:
        return None
    labels = ["0-30", "31-60", "61-90", "90+"]
    values = [flt(buckets.get(l, 0)) for l in labels]
    if all(v == 0 for v in values):
        return None
    return Chart(
        id="chart_get_receivables_aging",
        type="bar",
        title="Receivables Aging Buckets",
        data=ChartData(
            labels=labels,
            datasets=[{"label": "Outstanding", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_cash_and_bank_balance(result: dict) -> Chart | None:
    accounts = result.get("accounts") or []
    if not accounts:
        return None
    # Top 10 by absolute balance
    sorted_acc = sorted(accounts, key=lambda x: abs(flt(x["balance"])), reverse=True)[:10]
    labels = [a["account"] for a in sorted_acc]
    values = [flt(a["balance"]) for a in sorted_acc]
    return Chart(
        id="chart_get_cash_and_bank_balance",
        type="bar",
        title="Cash & Bank Balances (Top 10)",
        data=ChartData(
            labels=labels,
            datasets=[{"label": "Balance", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


# No chart for get_profit_and_loss_overview (just a few KPIs)


def _chart_get_expense_breakdown(result: dict) -> Chart | None:
    accounts = (result.get("top_accounts") or [])[:_MAX_CHART_BARS]
    if not accounts:
        return None
    labels = [a["account"] for a in accounts]
    values = [flt(a["amount"]) for a in accounts]
    return Chart(
        id="chart_get_expense_breakdown",
        type="bar",
        title="Top Expense Accounts",
        data=ChartData(
            labels=labels,
            datasets=[{"label": "Amount", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_trial_balance_summary(result: dict) -> Chart | None:
    categories = ["Assets", "Liabilities", "Equity", "Income (Period)", "Expense (Period)"]
    values = [
        flt(result.get("asset_balance", 0)),
        flt(result.get("liability_balance", 0)),
        flt(result.get("equity_balance", 0)),
        flt(result.get("income_balance", 0)),
        flt(result.get("expense_balance", 0)),
    ]
    if all(v == 0 for v in values):
        return None
    return Chart(
        id="chart_get_trial_balance_summary",
        type="bar",
        title="Trial Balance Summary",
        data=ChartData(
            labels=categories,
            datasets=[{"label": "Balance", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_tax_liability_overview(result: dict) -> Chart | None:
    accounts = (result.get("tax_accounts") or [])[:_MAX_CHART_BARS]
    if not accounts:
        return None
    labels = [a["account"] for a in accounts]
    values = [flt(a["balance"]) for a in accounts]
    return Chart(
        id="chart_get_tax_liability_overview",
        type="bar",
        title="Tax Account Balances",
        data=ChartData(
            labels=labels,
            datasets=[{"label": "Balance", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_payment_entry_summary(result: dict) -> Chart | None:
    by_mode = result.get("by_mode") or []
    if not by_mode:
        return None
    # Aggregate by mode_of_payment (sum amount across payment types)
    mode_totals: dict[str, float] = {}
    for row in by_mode:
        mode = row["mode_of_payment"]
        mode_totals[mode] = mode_totals.get(mode, 0) + flt(row["amount"])
    labels = list(mode_totals.keys())
    values = list(mode_totals.values())
    return Chart(
        id="chart_get_payment_entry_summary",
        type="pie",
        title="Payments by Mode",
        data=ChartData(
            labels=labels,
            datasets=[{"label": "Amount", "data": values}],
        ),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_branch_profit_and_loss(result: dict) -> Chart | None:
    branches = result.get("branches") or []
    if len(branches) < 2:
        return None
    labels = [b["branch"] for b in branches]
    data = [flt(b["net_profit"]) for b in branches]
    return Chart(
        id="chart_get_branch_profit_and_loss",
        title="Net Profit by Branch",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Net Profit", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_sales_trend(result: dict) -> Chart | None:
    """Line chart of net_sales over successive trend buckets (chronological)."""
    trend = result.get("trend") or []
    if len(trend) < 2:
        return None
    labels = [row["bucket"] for row in trend]
    data = [row["net_sales"] for row in trend]
    return Chart(
        id="chart_get_sales_trend",
        title="Sales Trend",
        type="line",
        data=ChartData(labels=labels, datasets=[{"label": "Net Sales", "data": data}]),
        options=ChartOptions(yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_hourly_sales_pattern(result: dict) -> Chart | None:
    """Bar chart of net_sales by weekday (7 bars, more legible than 24 hourly bars)."""
    by_weekday = result.get("by_weekday") or []
    if len(by_weekday) < 2:
        return None
    labels = [row["weekday"] for row in by_weekday]
    data = [row["net_sales"] for row in by_weekday]
    return Chart(
        id="chart_get_hourly_sales_pattern",
        title="Sales by Weekday",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Net Sales", "data": data}]),
        options=ChartOptions(yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_discount_overview(result: dict) -> Chart | None:
    breakdown = (result.get("branch_breakdown") or [])[:_MAX_CHART_BARS]
    if len(breakdown) < 2:
        return None
    labels = [row["branch"] for row in breakdown]
    data = [row["discount_amount"] for row in breakdown]
    return Chart(
        id="chart_get_discount_overview",
        title="Discount by Branch",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Discount Amount", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_sales_by_item_group(result: dict) -> Chart | None:
    item_groups = (result.get("item_groups") or [])[:_MAX_CHART_BARS]
    if len(item_groups) < 2:
        return None
    labels = [row["item_group"] for row in item_groups]
    data = [row["sales_amount"] for row in item_groups]
    return Chart(
        id="chart_get_sales_by_item_group",
        title="Sales by Item Group",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Sales Amount", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_selling_below_cost(result: dict) -> Chart | None:
    items = (result.get("items") or [])[:_MAX_CHART_BARS]
    if len(items) < 2:
        return None
    labels = [row["item_name"] for row in items]
    data = [row["loss_per_unit"] for row in items]
    return Chart(
        id="chart_get_selling_below_cost",
        title="Loss per Unit — Items Below Cost",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Loss / Unit", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_supplier_price_comparison(result: dict) -> Chart | None:
    suppliers = (result.get("suppliers") or [])[:_MAX_CHART_BARS]
    if len(suppliers) < 2:
        return None
    labels = [row["supplier"] for row in suppliers]
    data = [row["rate"] for row in suppliers]
    return Chart(
        id="chart_get_supplier_price_comparison",
        title=f"Purchase Rate by Supplier — {result.get('item_name', result.get('item_code', ''))}",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Rate", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_po_receipt_variance(result: dict) -> None:
    return None


def _chart_get_purchase_concentration(result: dict) -> Chart | None:
    suppliers = (result.get("top_suppliers") or [])[:_MAX_CHART_BARS]
    if len(suppliers) < 2:
        return None
    labels = [row["supplier"] for row in suppliers]
    data = [row["amount"] for row in suppliers]
    return Chart(
        id="chart_get_purchase_concentration",
        title="Purchase Spend by Top Suppliers",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Amount", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "currency"}),
        auto_selected=True,
    )


def _chart_get_stock_aging(result: dict) -> Chart | None:
    items = [row for row in (result.get("items") or []) if row.get("days_since_last_receipt") is not None][:_MAX_CHART_BARS]
    if len(items) < 2:
        return None
    labels = [row["item_name"] for row in items]
    data = [row["days_since_last_receipt"] for row in items]
    return Chart(
        id="chart_get_stock_aging",
        title="Stock Aging — Days Since Last Receipt",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Days Since Receipt", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "number"}),
        auto_selected=True,
    )


def _chart_get_reorder_recommendations(result: dict) -> Chart | None:
    items = (result.get("items") or [])[:_MAX_CHART_BARS]
    if len(items) < 2:
        return None
    labels = [row["item_name"] for row in items]
    data = [row["days_of_stock"] for row in items]
    return Chart(
        id="chart_get_reorder_recommendations",
        title="Days of Stock Remaining — Reorder Candidates",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": "Days of Stock", "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": "number"}),
        auto_selected=True,
    )


def _chart_get_negative_stock_check(result: dict) -> None:
    return None


def _chart_get_customer_activity_segments(result: dict) -> Chart | None:
    segments = result.get("segments") or {}
    labels = ["New", "Active", "Lapsing", "Lost"]
    keys = ["new", "active", "lapsing", "lost"]
    values = [flt(segments.get(k, 0)) for k in keys]
    if all(v == 0 for v in values):
        return None
    return Chart(
        id="chart_get_customer_activity_segments",
        title="Customer Activity Segments",
        type="donut",
        data=ChartData(labels=labels, datasets=[{"label": "Customers", "data": values}]),
        options=ChartOptions(),
        auto_selected=True,
    )


def _chart_get_open_documents_overview(result: dict) -> None:
    return None


def _chart_run_analytics_query(result: dict) -> Chart | None:
    """Bar chart of the first requested measure across dimension groups - only
    when the query was actually grouped by a dimension (an ungrouped single
    total is KPI-shaped instead, see _kpis_for_run_analytics_query)."""
    rows = (result.get("rows") or [])[:_MAX_CHART_BARS]
    if result.get("dimension") is None or len(rows) < 2:
        return None
    measures = result.get("measures") or []
    if not measures:
        return None
    first_measure = measures[0]
    labels = [str(r.get("dimension_value")) for r in rows]
    data = [flt(r.get(first_measure)) for r in rows]
    fmt = "percent" if "pct" in first_measure else ("currency" if any(t in first_measure for t in ("amount", "sales", "value")) else "number")
    return Chart(
        id="chart_run_analytics_query",
        title=f"{first_measure.replace('_', ' ').title()} by {(result.get('dimension') or '').replace('_', ' ').title()}",
        type="bar",
        data=ChartData(labels=labels, datasets=[{"label": first_measure.replace("_", " ").title(), "data": data}]),
        options=ChartOptions(horizontal=True, yAxis={"format": fmt}),
        auto_selected=True,
    )


def _chart_drill_down_transactions(result: dict) -> None:
    return None


_CHART_DISPATCH: dict[str, callable] = {
    "get_sales_overview": _chart_get_sales_overview,
    "get_top_selling_items": _chart_get_top_selling_items,
    "get_inventory_vs_sales": _chart_get_inventory_vs_sales,
    "get_payment_mode_split": _chart_get_payment_mode_split,
    "get_returns_overview": _chart_get_returns_overview,
    "get_branch_comparison": _chart_get_branch_comparison,
    "rank_vendors": _chart_rank_vendors,
    "get_purchase_overview": _chart_get_purchase_overview,
    "get_active_shifts": _chart_get_active_shifts,
    "get_outstanding_payables_overview": _chart_get_outstanding_payables_overview,
    "get_gross_margin_overview": _chart_get_gross_margin_overview,
    "get_dead_stock_detail": _chart_get_dead_stock_detail,
    "get_price_increases": _chart_get_price_increases,
    "get_top_customers": _chart_get_top_customers,
    "get_item_price_history": _chart_get_item_price_history,
    "get_receivables_overview": _chart_get_receivables_overview,
    "get_payables_aging": _chart_get_payables_aging,
    "get_receivables_aging": _chart_get_receivables_aging,
    "get_cash_and_bank_balance": _chart_get_cash_and_bank_balance,
    "get_expense_breakdown": _chart_get_expense_breakdown,
    "get_trial_balance_summary": _chart_get_trial_balance_summary,
    "get_tax_liability_overview": _chart_get_tax_liability_overview,
    "get_payment_entry_summary": _chart_get_payment_entry_summary,
    "get_branch_profit_and_loss": _chart_get_branch_profit_and_loss,
    "get_sales_trend": _chart_get_sales_trend,
    "get_hourly_sales_pattern": _chart_get_hourly_sales_pattern,
    "get_discount_overview": _chart_get_discount_overview,
    "get_sales_by_item_group": _chart_get_sales_by_item_group,
    "get_selling_below_cost": _chart_get_selling_below_cost,
    "get_supplier_price_comparison": _chart_get_supplier_price_comparison,
    "get_po_receipt_variance": _chart_get_po_receipt_variance,
    "get_purchase_concentration": _chart_get_purchase_concentration,
    "get_stock_aging": _chart_get_stock_aging,
    "get_reorder_recommendations": _chart_get_reorder_recommendations,
    "get_negative_stock_check": _chart_get_negative_stock_check,
    "get_customer_activity_segments": _chart_get_customer_activity_segments,
    "get_open_documents_overview": _chart_get_open_documents_overview,
    "run_analytics_query": _chart_run_analytics_query,
    "drill_down_transactions": _chart_drill_down_transactions,
}


def recommend_chart(tool_name: str, result: dict) -> Chart | None:
    """Public dispatcher: returns a Chart for the given tool+result, or None."""
    fn = _CHART_DISPATCH.get(tool_name)
    if fn is None:
        return None
    return fn(result)
