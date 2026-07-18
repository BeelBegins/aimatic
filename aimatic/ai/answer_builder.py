"""Phase 1 response assembly: deterministic KPI/table extraction per tool (same
philosophy as chart_recommender) and final StructuredResponse construction.
Confidence/intent/entities are honest placeholders, not real ML, until a later phase.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, now, today

from aimatic.ai.response_schema import (
    KPI,
    Table,
    TableColumn,
    Chart,
    Insight,
    Warning,
    Source,
    Action,
    StructuredResponse,
    Answer,
    Context,
    DateRange,
    ComparisonPeriod,
    Permissions,
)
from aimatic.ai.chart_recommender import recommend_chart
from aimatic.ai.insight_generator import generate_insights
from aimatic.ai.report_registry import get_registry


# ═══════════════════════════════════════════════════════════════════════════
# Part 1 — KPI extraction (dispatch dict pattern, one function per tool)
# ═══════════════════════════════════════════════════════════════════════════

def _kpis_for_get_sales_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (net_sales := result.get("net_sales")) is not None:
        kpis.append(KPI(
            key="get_sales_overview_net_sales",
            label="Net Sales",
            value=flt(net_sales),
            format="currency",
            currency=currency,
        ))
    if (avg_basket := result.get("average_basket")) is not None:
        kpis.append(KPI(
            key="get_sales_overview_average_basket",
            label="Average Basket",
            value=flt(avg_basket),
            format="currency",
            currency=currency,
        ))
    if (txn_count := result.get("txn_count")) is not None:
        kpis.append(KPI(
            key="get_sales_overview_txn_count",
            label="Transaction Count",
            value=flt(txn_count),
            format="number",
        ))
    return kpis


def _kpis_for_get_purchase_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (purchase_amount := result.get("purchase_amount")) is not None:
        kpis.append(KPI(
            key="get_purchase_overview_purchase_amount",
            label="Purchase Amount",
            value=flt(purchase_amount),
            format="currency",
            currency=currency,
        ))
    if (outstanding := result.get("outstanding_amount")) is not None:
        kpis.append(KPI(
            key="get_purchase_overview_outstanding_amount",
            label="Outstanding Payable",
            value=flt(outstanding),
            format="currency",
            currency=currency,
            severity="watch" if flt(outstanding) > 0 else None,
        ))
    return kpis


def _kpis_for_rank_vendors(result: dict) -> list[KPI]:
    return []


def _kpis_for_get_inventory_vs_sales(result: dict) -> list[KPI]:
    return []


def _kpis_for_get_branch_comparison(result: dict) -> list[KPI]:
    return []


def _kpis_for_get_payment_mode_split(result: dict) -> list[KPI]:
    return []


def _kpis_for_get_returns_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (returns_amount := result.get("returns_amount")) is not None:
        returns_count = result.get("returns_count", 0)
        kpis.append(KPI(
            key="get_returns_overview_returns_amount",
            label="Returns Amount",
            value=flt(returns_amount),
            format="currency",
            currency=currency,
            severity="watch" if flt(returns_count) > 0 else None,
        ))
    return kpis


def _kpis_for_get_active_shifts(result: dict) -> list[KPI]:
    active_shifts = result.get("active_shifts") or []
    kpis: list[KPI] = []
    kpis.append(KPI(
        key="get_active_shifts_active_shift_count",
        label="Active Shifts",
        value=float(len(active_shifts)),
        format="number",
    ))
    return kpis


def _kpis_for_get_top_selling_items(result: dict) -> list[KPI]:
    return []


def _kpis_for_get_outstanding_payables_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (total_outstanding := result.get("total_outstanding_amount")) is not None:
        kpis.append(KPI(
            key="get_outstanding_payables_overview_total_outstanding_amount",
            label="Total Outstanding Payable",
            value=flt(total_outstanding),
            format="currency",
            currency=currency,
            severity="warning" if flt(total_outstanding) > 0 else None,
        ))
    return kpis


def _kpis_for_get_gross_margin_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (margin_pct := result.get("gross_margin_pct")) is not None:
        margin_pct_val = flt(margin_pct)
        if margin_pct_val < 5:
            severity = "critical"
        elif margin_pct_val < 15:
            severity = "warning"
        else:
            severity = None
        kpis.append(KPI(
            key="get_gross_margin_overview_gross_margin_pct",
            label="Gross Margin %",
            value=margin_pct_val,
            format="percent",
            severity=severity,
        ))
    if (margin_amount := result.get("gross_margin_amount")) is not None:
        kpis.append(KPI(
            key="get_gross_margin_overview_gross_margin_amount",
            label="Gross Margin Amount",
            value=flt(margin_amount),
            format="currency",
            currency=currency,
        ))
    return kpis


def _kpis_for_get_item_price_history(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    cost_stats = result.get("cost_stats") or {}
    if (latest := cost_stats.get("latest")) is not None:
        kpis.append(KPI(
            key="get_item_price_history_latest_purchase_cost",
            label="Latest Purchase Cost",
            value=flt(latest),
            format="currency",
            currency=currency,
        ))
    if (change_pct := cost_stats.get("change_pct")) is not None:
        change_pct_val = flt(change_pct)
        kpis.append(KPI(
            key="get_item_price_history_cost_change_pct",
            label="Cost Change %",
            value=change_pct_val,
            format="percent",
            severity="warning" if change_pct_val > 15 else None,
        ))
    return kpis


def _kpis_for_get_price_increases(result: dict) -> list[KPI]:
    items = result.get("items") or []
    kpis: list[KPI] = []
    kpis.append(KPI(
        key="get_price_increases_items_with_price_increases",
        label="Items with Price Increases",
        value=float(len(items)),
        format="number",
        severity="watch" if len(items) > 0 else None,
    ))
    return kpis


def _kpis_for_get_dead_stock_detail(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (total_dead_value := result.get("total_dead_stock_value")) is not None:
        kpis.append(KPI(
            key="get_dead_stock_detail_total_dead_stock_value",
            label="Total Dead Stock Value",
            value=flt(total_dead_value),
            format="currency",
            currency=currency,
            severity="warning" if flt(total_dead_value) > 0 else None,
        ))
    return kpis


def _kpis_for_get_top_customers(result: dict) -> list[KPI]:
    return []


def _kpis_for_get_receivables_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    if (total_outstanding := result.get("total_outstanding_amount")) is not None:
        kpis.append(KPI(
            key="get_receivables_overview_total_outstanding_amount",
            label="Total Outstanding Receivable",
            value=flt(total_outstanding),
            format="currency",
            currency=currency,
            severity="watch" if flt(total_outstanding) > 0 else None,
        ))
    return kpis


def _kpis_for_run_dynamic_report(result: dict) -> list[KPI]:
    """A single-row aggregate query (SUM/COUNT/AVG/... with no group_by) is
    KPI-shaped; anything else (plain rows, or a grouped aggregate with >1 row)
    is table-shaped and handled by _table_for_run_dynamic_report instead."""
    rows = result.get("rows") or []
    if len(rows) != 1 or "value" not in rows[0]:
        return []
    return [KPI(
        key="run_dynamic_report_value",
        label=f"{result.get('doctype', 'Query')} Result",
        value=flt(rows[0]["value"]),
        format="number",
    )]


def _kpis_for_get_payables_aging(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    total = result.get("total_outstanding_amount")
    if total is not None:
        kpis.append(KPI(
            key="get_payables_aging_total_outstanding",
            label="Total Outstanding Payable",
            value=flt(total),
            format="currency",
            currency=currency,
            severity="warning" if flt(total) > 0 else "info",
        ))
    # Overdue (31+ days) as a KPI
    overdue = flt(result.get("buckets", {}).get("31-60", 0)) + flt(result.get("buckets", {}).get("61-90", 0)) + flt(result.get("buckets", {}).get("90+", 0))
    if overdue:
        kpis.append(KPI(
            key="get_payables_aging_overdue",
            label="Overdue Payables (31+ days)",
            value=overdue,
            format="currency",
            currency=currency,
            severity="critical" if overdue > 0 else "info",
        ))
    return kpis


def _table_for_get_payables_aging(result: dict) -> Table | None:
    suppliers = result.get("top_suppliers") or []
    if not suppliers:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_payables_aging",
        title="Top Suppliers by Outstanding (Aged)",
        columns=[
            TableColumn(key="supplier", label="Supplier", type="link", doctype="Supplier"),
            TableColumn(key="outstanding_amount", label="Total Outstanding", type="currency", currency=currency),
            TableColumn(key="bucket_0_30", label="0-30 Days", type="currency", currency=currency),
            TableColumn(key="bucket_31_60", label="31-60 Days", type="currency", currency=currency),
            TableColumn(key="bucket_61_90", label="61-90 Days", type="currency", currency=currency),
            TableColumn(key="bucket_90_plus", label="90+ Days", type="currency", currency=currency),
        ],
        rows=suppliers,
    )


def _kpis_for_get_receivables_aging(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    total = result.get("total_outstanding_amount")
    if total is not None:
        kpis.append(KPI(
            key="get_receivables_aging_total_outstanding",
            label="Total Outstanding Receivable",
            value=flt(total),
            format="currency",
            currency=currency,
            severity="warning" if flt(total) > 0 else "info",
        ))
    overdue = flt(result.get("buckets", {}).get("31-60", 0)) + flt(result.get("buckets", {}).get("61-90", 0)) + flt(result.get("buckets", {}).get("90+", 0))
    if overdue:
        kpis.append(KPI(
            key="get_receivables_aging_overdue",
            label="Overdue Receivables (31+ days)",
            value=overdue,
            format="currency",
            currency=currency,
            severity="critical" if overdue > 0 else "info",
        ))
    return kpis


def _table_for_get_receivables_aging(result: dict) -> Table | None:
    customers = result.get("top_customers") or []
    if not customers:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_receivables_aging",
        title="Top Customers by Outstanding (Aged)",
        columns=[
            TableColumn(key="customer", label="Customer", type="link", doctype="Customer"),
            TableColumn(key="customer_name", label="Customer Name", type="text"),
            TableColumn(key="outstanding_amount", label="Total Outstanding", type="currency", currency=currency),
            TableColumn(key="bucket_0_30", label="0-30 Days", type="currency", currency=currency),
            TableColumn(key="bucket_31_60", label="31-60 Days", type="currency", currency=currency),
            TableColumn(key="bucket_61_90", label="61-90 Days", type="currency", currency=currency),
            TableColumn(key="bucket_90_plus", label="90+ Days", type="currency", currency=currency),
        ],
        rows=customers,
    )


def _kpis_for_get_cash_and_bank_balance(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    total = result.get("total_balance")
    if total is not None:
        kpis.append(KPI(
            key="get_cash_and_bank_balance_total",
            label="Total Cash & Bank Balance",
            value=flt(total),
            format="currency",
            currency=currency,
            severity="info",
        ))
    return kpis


def _table_for_get_cash_and_bank_balance(result: dict) -> Table | None:
    accounts = result.get("accounts") or []
    if not accounts:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_cash_and_bank_balance",
        title="Cash & Bank Account Balances",
        columns=[
            TableColumn(key="account", label="Account", type="link", doctype="Account"),
            TableColumn(key="account_type", label="Type", type="text"),
            TableColumn(key="balance", label="Balance", type="currency", currency=currency),
        ],
        rows=accounts,
    )


def _kpis_for_get_profit_and_loss_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    for key, label, severity in [
        ("total_income", "Total Income", "info"),
        ("total_expense", "Total Expense", "info"),
        ("net_profit", "Net Profit", "critical" if flt(result.get("net_profit", 0)) < 0 else "info"),
        ("net_margin_pct", "Net Margin %", "warning" if flt(result.get("net_margin_pct", 0)) < 10 else "info"),
    ]:
        val = result.get(key)
        if val is not None:
            fmt = "percent" if key == "net_margin_pct" else "currency"
            kpis.append(KPI(
                key=f"get_profit_and_loss_overview_{key}",
                label=label,
                value=flt(val),
                format=fmt,
                currency=currency if fmt == "currency" else None,
                severity=severity,
            ))
    return kpis


# No table for P&L overview (it's a few headline numbers)


def _kpis_for_get_expense_breakdown(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    total = result.get("total_expense")
    if total is not None:
        kpis.append(KPI(
            key="get_expense_breakdown_total_expense",
            label="Total Expense",
            value=flt(total),
            format="currency",
            currency=currency,
            severity="info",
        ))
    return kpis


def _table_for_get_expense_breakdown(result: dict) -> Table | None:
    accounts = result.get("top_accounts") or []
    if not accounts:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_expense_breakdown",
        title="Top Expense Accounts",
        columns=[
            TableColumn(key="account", label="Account", type="link", doctype="Account"),
            TableColumn(key="amount", label="Amount", type="currency", currency=currency),
        ],
        rows=accounts,
    )


def _kpis_for_get_trial_balance_summary(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    for key, label in [
        ("asset_balance", "Total Assets"),
        ("liability_balance", "Total Liabilities"),
        ("equity_balance", "Total Equity"),
        ("income_balance", "Period Income"),
        ("expense_balance", "Period Expense"),
        ("balance_check", "Balance Check (Assets - Liab - Equity)"),
    ]:
        val = result.get(key)
        if val is not None:
            # balance_check is expected to be nonzero in normal operation - it's
            # Assets minus (Liabilities + Equity), and Income/Expense are period
            # balances rather than being folded into Equity until a fiscal-year
            # closing entry runs. A large swing between calls can still be a
            # useful data-quality signal, but a flat nonzero value alone is not
            # an error, so this never escalates past "info".
            kpis.append(KPI(
                key=f"get_trial_balance_summary_{key}",
                label=label,
                value=flt(val),
                format="currency",
                currency=currency,
                severity="info",
            ))
    return kpis


def _table_for_get_trial_balance_summary(result: dict) -> Table | None:
    # We can present the five balances as a table
    rows = [
        {"category": "Assets", "balance": result.get("asset_balance", 0)},
        {"category": "Liabilities", "balance": result.get("liability_balance", 0)},
        {"category": "Equity", "balance": result.get("equity_balance", 0)},
        {"category": "Income (Period)", "balance": result.get("income_balance", 0)},
        {"category": "Expense (Period)", "balance": result.get("expense_balance", 0)},
    ]
    currency = result.get("currency")
    return Table(
        id="table_get_trial_balance_summary",
        title="Trial Balance Summary",
        columns=[
            TableColumn(key="category", label="Category", type="text"),
            TableColumn(key="balance", label="Balance", type="currency", currency=currency),
        ],
        rows=rows,
    )


def _kpis_for_get_tax_liability_overview(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    total = result.get("total_tax_balance")
    if total is not None:
        kpis.append(KPI(
            key="get_tax_liability_overview_total",
            label="Total Tax Liability",
            value=flt(total),
            format="currency",
            currency=currency,
            severity="warning" if flt(total) > 0 else "info",
        ))
    return kpis


def _table_for_get_tax_liability_overview(result: dict) -> Table | None:
    accounts = result.get("tax_accounts") or []
    if not accounts:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_tax_liability_overview",
        title="Tax Account Balances",
        columns=[
            TableColumn(key="account", label="Account", type="link", doctype="Account"),
            TableColumn(key="balance", label="Balance", type="currency", currency=currency),
        ],
        rows=accounts,
    )


def _kpis_for_get_payment_entry_summary(result: dict) -> list[KPI]:
    currency = result.get("currency")
    kpis: list[KPI] = []
    for key, label, sev in [
        ("total_received", "Total Received", "info"),
        ("total_paid", "Total Paid", "info"),
        ("net_cash_flow", "Net Cash Flow", "critical" if flt(result.get("net_cash_flow", 0)) < 0 else "info"),
    ]:
        val = result.get(key)
        if val is not None:
            kpis.append(KPI(
                key=f"get_payment_entry_summary_{key}",
                label=label,
                value=flt(val),
                format="currency",
                currency=currency,
                severity=sev,
            ))
    return kpis


def _table_for_get_payment_entry_summary(result: dict) -> Table | None:
    by_mode = result.get("by_mode") or []
    if not by_mode:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_payment_entry_summary",
        title="Payments by Mode",
        columns=[
            TableColumn(key="mode_of_payment", label="Mode of Payment", type="text"),
            TableColumn(key="payment_type", label="Type", type="text"),
            TableColumn(key="amount", label="Amount", type="currency", currency=currency),
            TableColumn(key="txn_count", label="Transactions", type="int"),
        ],
        rows=by_mode,
    )


def _kpis_for_get_branch_profit_and_loss(result: dict) -> list[KPI]:
    total_net_profit = flt(result.get("total_net_profit", 0))
    currency = result.get("currency")
    return [
        KPI(
            key="get_branch_profit_and_loss_total_net_profit",
            label="Total Net Profit (All Branches)",
            value=total_net_profit,
            format="currency",
            currency=currency,
            severity="critical" if total_net_profit < 0 else "info",
        )
    ]


def _table_for_get_branch_profit_and_loss(result: dict) -> Table | None:
    branches = result.get("branches") or []
    if not branches:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_branch_profit_and_loss",
        title="Profit & Loss by Branch",
        columns=[
            TableColumn(key="branch", label="Branch", type="text"),
            TableColumn(key="total_income", label="Total Income", type="currency", currency=currency),
            TableColumn(key="total_expense", label="Total Expense", type="currency", currency=currency),
            TableColumn(key="net_profit", label="Net Profit", type="currency", currency=currency),
            TableColumn(key="net_margin_pct", label="Net Margin %", type="percent"),
        ],
        rows=branches,
    )


_KPI_DISPATCH: dict[str, callable] = {
    "run_dynamic_report": _kpis_for_run_dynamic_report,
    "get_sales_overview": _kpis_for_get_sales_overview,
    "get_purchase_overview": _kpis_for_get_purchase_overview,
    "rank_vendors": _kpis_for_rank_vendors,
    "get_inventory_vs_sales": _kpis_for_get_inventory_vs_sales,
    "get_branch_comparison": _kpis_for_get_branch_comparison,
    "get_payment_mode_split": _kpis_for_get_payment_mode_split,
    "get_returns_overview": _kpis_for_get_returns_overview,
    "get_active_shifts": _kpis_for_get_active_shifts,
    "get_top_selling_items": _kpis_for_get_top_selling_items,
    "get_outstanding_payables_overview": _kpis_for_get_outstanding_payables_overview,
    "get_gross_margin_overview": _kpis_for_get_gross_margin_overview,
    "get_item_price_history": _kpis_for_get_item_price_history,
    "get_price_increases": _kpis_for_get_price_increases,
    "get_dead_stock_detail": _kpis_for_get_dead_stock_detail,
    "get_top_customers": _kpis_for_get_top_customers,
    "get_receivables_overview": _kpis_for_get_receivables_overview,
    "get_payables_aging": _kpis_for_get_payables_aging,
    "get_receivables_aging": _kpis_for_get_receivables_aging,
    "get_cash_and_bank_balance": _kpis_for_get_cash_and_bank_balance,
    "get_profit_and_loss_overview": _kpis_for_get_profit_and_loss_overview,
    "get_expense_breakdown": _kpis_for_get_expense_breakdown,
    "get_trial_balance_summary": _kpis_for_get_trial_balance_summary,
    "get_tax_liability_overview": _kpis_for_get_tax_liability_overview,
    "get_payment_entry_summary": _kpis_for_get_payment_entry_summary,
    "get_branch_profit_and_loss": _kpis_for_get_branch_profit_and_loss,
}


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 — Table extraction (dispatch dict pattern, one function per tool)
# ═══════════════════════════════════════════════════════════════════════════

def _table_for_get_sales_overview(result: dict) -> Table | None:
    breakdown = result.get("branch_breakdown") or []
    if not breakdown:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_sales_overview",
        title="Sales by Branch",
        columns=[
            TableColumn(key="branch", label="Branch", type="text"),
            TableColumn(key="net_sales", label="Net Sales", type="currency", currency=currency),
        ],
        rows=breakdown,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_inventory_vs_sales(result: dict) -> Table | None:
    items = result.get("items") or []
    if not items:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_inventory_vs_sales",
        title="Inventory vs Sales",
        columns=[
            TableColumn(key="item_code", label="Item Code", type="link", doctype="Item"),
            TableColumn(key="item_name", label="Item Name", type="text"),
            TableColumn(key="stock_qty", label="Stock Qty", type="float"),
            TableColumn(key="stock_value", label="Stock Value", type="currency", currency=currency),
            TableColumn(key="days_of_stock", label="Days of Stock", type="float"),
        ],
        rows=items,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_branch_comparison(result: dict) -> Table | None:
    branches = result.get("branches") or []
    if not branches:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_branch_comparison",
        title="Branch Comparison",
        columns=[
            TableColumn(key="branch", label="Branch", type="text"),
            TableColumn(key="net_sales", label="Net Sales", type="currency", currency=currency),
        ],
        rows=branches,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_payment_mode_split(result: dict) -> Table | None:
    split = result.get("payment_split") or []
    if not split:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_payment_mode_split",
        title="Payment Mode Split",
        columns=[
            TableColumn(key="mode_of_payment", label="Mode of Payment", type="text"),
            TableColumn(key="amount", label="Amount", type="currency", currency=currency),
        ],
        rows=split,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_returns_overview(result: dict) -> Table | None:
    breakdown = result.get("branch_breakdown") or []
    if not breakdown:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_returns_overview",
        title="Returns by Branch",
        columns=[
            TableColumn(key="branch", label="Branch", type="text"),
            TableColumn(key="returns_amount", label="Returns Amount", type="currency", currency=currency),
            TableColumn(key="returns_count", label="Returns Count", type="int"),
        ],
        rows=breakdown,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_active_shifts(result: dict) -> Table | None:
    shifts = result.get("active_shifts") or []
    if not shifts:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_active_shifts",
        title="Active POS Shifts",
        columns=[
            TableColumn(key="pos_profile", label="POS Profile", type="text"),
            TableColumn(key="branch", label="Branch", type="text"),
            TableColumn(key="user", label="User", type="text"),
            TableColumn(key="running_total", label="Running Total", type="currency", currency=currency),
            TableColumn(key="txn_count", label="Transaction Count", type="int"),
        ],
        rows=shifts,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_top_selling_items(result: dict) -> Table | None:
    items = result.get("items") or []
    if not items:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_top_selling_items",
        title="Top Selling Items",
        columns=[
            TableColumn(key="item_code", label="Item Code", type="link", doctype="Item"),
            TableColumn(key="item_name", label="Item Name", type="text"),
            TableColumn(key="sales_qty", label="Sales Qty", type="float"),
            TableColumn(key="sales_amount", label="Sales Amount", type="currency", currency=currency),
        ],
        rows=items,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_outstanding_payables_overview(result: dict) -> Table | None:
    suppliers = result.get("top_suppliers") or []
    if not suppliers:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_outstanding_payables_overview",
        title="Top Suppliers by Outstanding",
        columns=[
            TableColumn(key="supplier", label="Supplier", type="link", doctype="Supplier"),
            TableColumn(key="outstanding_amount", label="Outstanding Amount", type="currency", currency=currency),
            TableColumn(key="invoice_count", label="Invoice Count", type="int"),
        ],
        rows=suppliers,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_item_price_history(result: dict) -> Table | None:
    history = result.get("purchase_cost_history") or []
    if not history:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_item_price_history",
        title="Purchase Cost History",
        columns=[
            TableColumn(key="date", label="Date", type="date"),
            TableColumn(key="doc_type", label="Document Type", type="text"),
            TableColumn(key="doc_name", label="Document", type="text"),
            TableColumn(key="supplier", label="Supplier", type="text"),
            TableColumn(key="price_after_taxes", label="Price After Taxes", type="currency", currency=currency),
        ],
        rows=history,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_price_increases(result: dict) -> Table | None:
    items = result.get("items") or []
    if not items:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_price_increases",
        title="Items with Price Increases",
        columns=[
            TableColumn(key="item_code", label="Item Code", type="link", doctype="Item"),
            TableColumn(key="item_name", label="Item Name", type="text"),
            TableColumn(key="old_price", label="Old Price", type="currency", currency=currency),
            TableColumn(key="new_price", label="New Price", type="currency", currency=currency),
            TableColumn(key="change_pct", label="Change %", type="percent"),
            TableColumn(key="supplier", label="Supplier", type="text"),
        ],
        rows=items,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_dead_stock_detail(result: dict) -> Table | None:
    items = result.get("items") or []
    if not items:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_dead_stock_detail",
        title="Dead Stock Detail",
        columns=[
            TableColumn(key="item_code", label="Item Code", type="link", doctype="Item"),
            TableColumn(key="item_name", label="Item Name", type="text"),
            TableColumn(key="stock_qty", label="Stock Qty", type="float"),
            TableColumn(key="stock_value", label="Stock Value", type="currency", currency=currency),
            TableColumn(key="days_since_last_sale", label="Days Since Last Sale", type="int"),
        ],
        rows=items,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_top_customers(result: dict) -> Table | None:
    customers = result.get("customers") or []
    if not customers:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_top_customers",
        title="Top Customers",
        columns=[
            TableColumn(key="customer", label="Customer", type="link", doctype="Customer"),
            TableColumn(key="customer_name", label="Customer Name", type="text"),
            TableColumn(key="sales_amount", label="Sales Amount", type="currency", currency=currency),
            TableColumn(key="txn_count", label="Transaction Count", type="int"),
        ],
        rows=customers,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_get_receivables_overview(result: dict) -> Table | None:
    customers = result.get("top_customers") or []
    if not customers:
        return None
    currency = result.get("currency")
    return Table(
        id="table_get_receivables_overview",
        title="Top Customers by Outstanding",
        columns=[
            TableColumn(key="customer", label="Customer", type="link", doctype="Customer"),
            TableColumn(key="customer_name", label="Customer Name", type="text"),
            TableColumn(key="outstanding_amount", label="Outstanding Amount", type="currency", currency=currency),
            TableColumn(key="invoice_count", label="Invoice Count", type="int"),
        ],
        rows=customers,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_rank_vendors(result: dict) -> Table | None:
    vendors = result.get("vendors") or []
    if not vendors:
        return None
    currency = result.get("currency")
    return Table(
        id="table_rank_vendors",
        title="Vendor Ranking",
        columns=[
            TableColumn(key="supplier", label="Supplier", type="link", doctype="Supplier"),
            TableColumn(key="gross_margin_pct", label="Gross Margin %", type="percent"),
            TableColumn(key="purchase_amount", label="Purchase Amount", type="currency", currency=currency),
            TableColumn(key="outstanding_amount", label="Outstanding Amount", type="currency", currency=currency),
        ],
        rows=vendors,
        sortable=True,
        filterable=True,
        exportable=True,
    )


def _table_for_run_dynamic_report(result: dict) -> Table | None:
    """Generic table for the one dynamic_report.py fallback tool - unlike every
    other tool here, its row shape isn't known ahead of time (it depends on
    whatever fields/aggregation the model requested within the whitelist), so
    columns are inferred from the first row's keys with a small type map for
    known field-name conventions already used elsewhere in this app."""
    rows = result.get("rows") or []
    if not rows:
        return None
    currency_fields = {"grand_total", "base_grand_total", "outstanding_amount", "value", "custom_mrp"}
    link_fields = {"supplier": "Supplier", "customer": "Customer", "branch": "Branch", "pos_profile": "POS Profile", "name": result.get("doctype")}
    date_fields = {"posting_date"}
    columns = []
    for key in rows[0].keys():
        if key in date_fields:
            columns.append(TableColumn(key=key, label=key.replace("_", " ").title(), type="date"))
        elif key in currency_fields:
            columns.append(TableColumn(key=key, label=key.replace("_", " ").title(), type="currency", currency=None))
        elif key in link_fields and link_fields[key]:
            columns.append(TableColumn(key=key, label=key.replace("_", " ").title(), type="link", doctype=link_fields[key]))
        else:
            columns.append(TableColumn(key=key, label=key.replace("_", " ").title(), type="text"))
    return Table(
        id="table_run_dynamic_report",
        title=f"{result.get('doctype', 'Query')} Results",
        columns=columns,
        rows=rows,
        sortable=True,
        filterable=True,
        exportable=True,
    )


_TABLE_DISPATCH: dict[str, callable] = {
    "run_dynamic_report": _table_for_run_dynamic_report,
    "get_sales_overview": _table_for_get_sales_overview,
    "get_inventory_vs_sales": _table_for_get_inventory_vs_sales,
    "get_branch_comparison": _table_for_get_branch_comparison,
    "get_payment_mode_split": _table_for_get_payment_mode_split,
    "get_returns_overview": _table_for_get_returns_overview,
    "get_active_shifts": _table_for_get_active_shifts,
    "get_top_selling_items": _table_for_get_top_selling_items,
    "get_outstanding_payables_overview": _table_for_get_outstanding_payables_overview,
    "get_item_price_history": _table_for_get_item_price_history,
    "get_price_increases": _table_for_get_price_increases,
    "get_dead_stock_detail": _table_for_get_dead_stock_detail,
    "get_top_customers": _table_for_get_top_customers,
    "get_receivables_overview": _table_for_get_receivables_overview,
    "rank_vendors": _table_for_rank_vendors,
    # Tools with no list-shaped data return None implicitly
    "get_purchase_overview": lambda r: None,
    "get_gross_margin_overview": lambda r: None,
    "get_payables_aging": _table_for_get_payables_aging,
    "get_receivables_aging": _table_for_get_receivables_aging,
    "get_cash_and_bank_balance": _table_for_get_cash_and_bank_balance,
    "get_expense_breakdown": _table_for_get_expense_breakdown,
    "get_trial_balance_summary": _table_for_get_trial_balance_summary,
    "get_tax_liability_overview": _table_for_get_tax_liability_overview,
    "get_payment_entry_summary": _table_for_get_payment_entry_summary,
    "get_branch_profit_and_loss": _table_for_get_branch_profit_and_loss,
}


# ═══════════════════════════════════════════════════════════════════════════
# Part 3 — Orchestrator
# ═══════════════════════════════════════════════════════════════════════════

def build_response(
    question: str,
    reply_text: str,
    tool_results: dict[str, dict],
    company: str,
    branch_names: list[str],
    user_role: str,
) -> StructuredResponse:
    """Assemble a complete StructuredResponse from the LLM's text reply and tool results."""
    # ─── Collect KPIs, Charts, Tables ───
    all_kpis: list[KPI] = []
    all_charts: list[Chart] = []
    all_tables: list[Table] = []

    for tool_name, result in tool_results.items():
        # KPIs
        kpi_fn = _KPI_DISPATCH.get(tool_name)
        if kpi_fn:
            all_kpis.extend(kpi_fn(result))

        # Chart
        chart = recommend_chart(tool_name, result)
        if chart:
            all_charts.append(chart)

        # Table
        table_fn = _TABLE_DISPATCH.get(tool_name)
        if table_fn:
            table = table_fn(result)
            if table:
                all_tables.append(table)

    # ─── Insights ───
    insights = generate_insights(tool_results)

    # ─── Sources ───
    registry = get_registry()
    sources: list[Source] = []
    for tool_name in tool_results.keys():
        ds = registry.get(tool_name)
        if ds:
            sources.append(Source(
                type="tool",
                name=ds.name,
                description=ds.description,
            ))

    # ─── Follow-up questions (rule-based, Phase 1) ───
    follow_ups: list[str] = []
    tool_names = set(tool_results.keys())

    sales_tools = {
        "get_sales_overview", "get_branch_comparison", "get_top_selling_items",
        "get_payment_mode_split", "get_returns_overview", "get_active_shifts",
    }
    inventory_tools = {
        "get_inventory_vs_sales", "get_dead_stock_detail", "get_item_price_history",
        "get_price_increases",
    }
    purchase_tools = {
        "get_purchase_overview", "rank_vendors", "get_outstanding_payables_overview",
    }
    customer_tools = {
        "get_top_customers", "get_receivables_overview",
    }
    margin_tools = {"get_gross_margin_overview"}

    if tool_names & sales_tools:
        follow_ups.append("Compare with last month")
    if tool_names & inventory_tools:
        follow_ups.append("Show items at risk of stockout")
    if tool_names & purchase_tools:
        follow_ups.append("Show supplier payment terms")
    if tool_names & customer_tools:
        follow_ups.append("Show customer aging report")
    if tool_names & margin_tools:
        follow_ups.append("Break down margin by category")

    # Always include export affordance (forward-looking; not implemented yet -
    # no export backend exists in Phase 1, this is just a suggested question the
    # user can type, not a working action - see actions=[] below)
    follow_ups.append("Export this to Excel")

    # ─── Warnings ───
    warnings: list[Warning] = []
    if not tool_results:
        warnings.append(Warning(
            code="NO_TOOL_DATA",
            message="This answer is based on general knowledge, not live ERPNext data.",
            affected_metrics=[],
        ))

    # ─── Actions ───
    # Always empty in Phase 1: no save/export/schedule backend exists yet, so we
    # don't fabricate action affordances the UI can't actually fulfill.
    actions: list[Action] = []

    # ─── Answer ───
    title_source = reply_text.strip() or question.strip()
    title = title_source[:60] + ("..." if len(title_source) > 60 else "")
    confidence = 0.85 if tool_results else 0.5  # Phase-1 placeholder heuristic, not a real ML score
    data_quality = "good" if tool_results else "fair"

    answer = Answer(
        title=title,
        summary=reply_text,
        confidence=confidence,
        data_quality=data_quality,
        intent="general",  # Phase 1 has no real intent classifier yet - placeholder
        entities={},  # Phase 1 has no real entity extractor yet - placeholder
    )

    # ─── Context ───
    date_from = date_to = str(today())
    for result in tool_results.values():
        if result.get("date_from") and result.get("date_to"):
            date_from = str(result["date_from"])
            date_to = str(result["date_to"])
            break

    context = Context(
        company=company,
        branch=branch_names,
        date_range=DateRange(from_=date_from, to=date_to),
        filters={},
        comparison_period=None,  # Phase 1 has no comparison-period support yet
        user_role=user_role,
        data_freshness=str(now()),
        permissions=Permissions(
            can_export=False,  # no export backend exists yet - honest, not aspirational
            can_schedule=False,  # no schedule backend exists yet
            branches_visible=len(branch_names),
            branches_total=len(branch_names),
        ),
    )

    return StructuredResponse(
        answer=answer,
        context=context,
        kpis=all_kpis,
        charts=all_charts,
        tables=all_tables,
        insights=insights,
        warnings=warnings,
        follow_up_questions=follow_ups,
        sources=sources,
        actions=actions,
    )
