"""Deterministic chart recommendation per tool result - no LLM, no guessing.

Given a tool name and its already-executed result dict, returns a single
response_schema.Chart or None. Each tool's output shape is known exactly from
tools.py, so logic is per-tool and explicit.
"""

from __future__ import annotations

from aimatic.ai.response_schema import Chart, ChartData, ChartOptions

# Bar charts stay readable up to about this many bars before labels get
# truncated illegibly - tools like get_dead_stock_detail/get_price_increases
# can return up to 100 rows for their table, but the chart only ever shows
# the top N (already sorted by relevance by the tool itself).
_MAX_CHART_BARS = 10


def _chart_get_sales_overview(result: dict) -> Chart | None:
    """Horizontal bar of net_sales by branch from branch_breakdown."""
    breakdown = result.get("branch_breakdown") or []
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
}


def recommend_chart(tool_name: str, result: dict) -> Chart | None:
    """Public dispatcher: returns a Chart for the given tool+result, or None."""
    fn = _CHART_DISPATCH.get(tool_name)
    if fn is None:
        return None
    return fn(result)
