"""Phase 4c+4d: governed analytics query engine + drill-down - the "semantic layer"
that generalizes dynamic_report.py's per-doctype field whitelist into a business-
measure/dimension whitelist, so the model can answer "sales by branch this month vs
last month" or "outstanding payable by aging bucket" style questions without a
dedicated tool existing for every combination.

SECURITY INVARIANTS (same class as dynamic_report.py - do not weaken):
  - Every measure/dimension name is looked up in a fixed Python dict mapping to a
    pre-written SQL expression BEFORE use. No LLM-supplied string ever reaches SQL.
  - Company scoping is always resolved server-side via tools._resolve_company() and
    force-applied. Branch scoping is force-applied via tools._resolve_branch_filter()
    for any branch-restricted user.
  - All SQL is parameterized (frappe.db.sql with a params dict) - no interpolated
    filter *values*, only interpolated (whitelisted) column expressions.
  - Row/dimension-group limits are always server-enforced.
  - Each dataset reuses the same underlying business logic already proven correct
    elsewhere in this module (revenue-double-counting fix, GL sign conventions,
    positive-balance-only payables filter) rather than re-deriving it - see each
    dataset's own docstring for which existing tool it mirrors.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

from aimatic.ai.tools import _resolve_company, _resolve_branch_filter, _branch_warehouses, _get_date_range

_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MAX_LIMIT = 100
_DEFAULT_LIMIT = 50


# ═══════════════════════════════════════════════════════════════════════════
# Dataset: sales (mirrors tools.get_sales_overview's revenue source - POS
# Invoice only, never unioned with Sales Invoice - see the revenue double-
# counting gotcha documented throughout this app)
# ═══════════════════════════════════════════════════════════════════════════

_SALES_MEASURES = {
    "net_sales": "COALESCE(SUM(pi.grand_total), 0)",
    "gross_sales": "COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN pi.grand_total END), 0)",
    "returns_amount": "COALESCE(SUM(CASE WHEN pi.is_return = 1 THEN -pi.grand_total END), 0)",
    "txn_count": "COUNT(*)",
    "avg_basket": "COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN pi.grand_total END) / NULLIF(SUM(CASE WHEN pi.is_return = 0 THEN 1 ELSE 0 END), 0), 0)",
}
_SALES_DIMENSIONS = {
    "branch": "COALESCE(pi.branch, pp.branch)",
    "item_group": "i.item_group",
    "customer_group": "COALESCE(cust.customer_group, 'Unknown')",
    "month": "DATE_FORMAT(pi.posting_date, '%Y-%m-01')",
    "day_of_week": "WEEKDAY(pi.posting_date)",
}


def _query_sales(measures: list[str], dimension: str | None, date_from, date_to, branch_filter) -> list[dict]:
    dim_expr = _SALES_DIMENSIONS[dimension] if dimension else "'Total'"
    measure_exprs = _SALES_MEASURES
    item_joins = ""
    if dimension == "item_group":
        # Header totals cannot be grouped after joining item rows without
        # multiplying invoices. For this dimension use the line-level monetary
        # share and distinct invoice counts instead. Allocate each invoice's
        # grand total proportionally by the line's base net amount so grouped
        # net_sales reconciles exactly to the header-level certified measure
        # (plain SUM(base_net_amount) would silently exclude taxes).
        allocated_total = "(pii.base_net_amount / NULLIF(pi.base_net_total, 0)) * pi.grand_total"
        measure_exprs = {
            "net_sales": f"COALESCE(SUM({allocated_total}), 0)",
            "gross_sales": f"COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN {allocated_total} END), 0)",
            "returns_amount": f"COALESCE(SUM(CASE WHEN pi.is_return = 1 THEN -({allocated_total}) END), 0)",
            "txn_count": "COUNT(DISTINCT pi.name)",
            "avg_basket": f"COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN {allocated_total} END) / NULLIF(COUNT(DISTINCT CASE WHEN pi.is_return = 0 THEN pi.name END), 0), 0)",
        }
        item_joins = "INNER JOIN `tabPOS Invoice Item` pii ON pii.parent = pi.name INNER JOIN `tabItem` i ON i.name = pii.item_code"
    select_measures = ", ".join(f"{measure_exprs[m]} AS {m}" for m in measures)
    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    rows = frappe.db.sql(
        f"""
        SELECT {dim_expr} AS dimension_value, {select_measures}
        FROM `tabPOS Invoice` pi
        {item_joins}
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        LEFT JOIN `tabCustomer` cust ON cust.name = pi.customer
        WHERE pi.docstatus = 1 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY dimension_value
        ORDER BY dimension_value
        """,
        {"company": _resolve_company(), "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )
    if dimension == "day_of_week":
        for r in rows:
            r["dimension_value"] = _WEEKDAY_NAMES[cint(r["dimension_value"])]
    return [{k: (flt(v) if k != "dimension_value" else v) for k, v in r.items()} for r in rows]


# ═══════════════════════════════════════════════════════════════════════════
# Dataset: purchases (kept as two distinct measures rather than folded into
# one "spend" figure - same reasoning tools.get_purchase_overview already
# documents: billed amount and received cost basis can genuinely diverge)
# ═══════════════════════════════════════════════════════════════════════════

_PURCHASE_DIMENSIONS = {"branch": "branch", "supplier": "supplier", "month": "DATE_FORMAT(posting_date, '%Y-%m-01')"}
_GL_PURCHASE_DIMENSIONS = {"branch": "branch", "supplier": "party"}  # outstanding_amount is a balance, not month-bucketable


def _query_purchase_measure(table: str, dimension: str | None, date_from, date_to, branch_filter, supplier: str | None) -> dict[str, float]:
    dim_expr = _PURCHASE_DIMENSIONS[dimension] if dimension else "'Total'"
    branch_clause = "AND branch IN %(branch_names)s" if branch_filter is not None else ""
    supplier_clause = "AND supplier = %(supplier)s" if supplier else ""
    rows = frappe.db.sql(
        f"""
        SELECT {dim_expr} AS dimension_value, COALESCE(SUM(base_grand_total), 0) AS value
        FROM `tab{table}`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause} {supplier_clause}
        GROUP BY dimension_value
        """,
        {"company": _resolve_company(), "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else (), "supplier": supplier},
        as_dict=True,
    )
    return {(r.dimension_value or _("Unassigned")): flt(r.value) for r in rows}


def _query_purchase_outstanding(dimension: str | None, date_to, branch_filter, supplier: str | None) -> dict[str, float]:
    branch_clause = "AND branch IN %(branch_names)s" if branch_filter is not None else ""
    supplier_clause = "AND party = %(supplier)s" if supplier else ""
    rows = frappe.db.sql(
        f"""
        SELECT party, branch, SUM(credit - debit) AS value
        FROM `tabGL Entry`
        WHERE party_type = 'Supplier' AND is_cancelled = 0 AND company = %(company)s
          AND posting_date <= %(date_to)s
          {branch_clause} {supplier_clause}
        GROUP BY party, branch
        """,
        {"company": _resolve_company(), "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else (), "supplier": supplier},
        as_dict=True,
    )
    party_totals: dict[str, float] = {}
    for row in rows:
        party_totals[row.party] = party_totals.get(row.party, 0.0) + flt(row.value)

    if dimension == "supplier":
        return {party: value for party, value in party_totals.items() if value > 0}
    if dimension == "branch":
        branch_totals: dict[str, float] = {}
        for row in rows:
            # Preserve the established positive-net-supplier convention within
            # each branch instead of allowing one prepaid supplier to reduce
            # what is owed to another supplier in that branch.
            if flt(row.value) <= 0:
                continue
            key = row.branch or _("Unassigned")
            branch_totals[key] = branch_totals.get(key, 0.0) + flt(row.value)
        return branch_totals
    return {"Total": sum(value for value in party_totals.values() if value > 0)}


def _query_purchases(measures: list[str], dimension: str | None, date_from, date_to, branch_filter, supplier: str | None) -> list[dict]:
    if "outstanding_amount" in measures and dimension not in _GL_PURCHASE_DIMENSIONS:
        if dimension is not None:
            raise ValueError(f"outstanding_amount is a balance, not a flow - it can only be grouped by {sorted(_GL_PURCHASE_DIMENSIONS)}, not '{dimension}'.")

    per_measure: dict[str, dict[str, float]] = {}
    if "purchase_invoice_amount" in measures:
        per_measure["purchase_invoice_amount"] = _query_purchase_measure("Purchase Invoice", dimension, date_from, date_to, branch_filter, supplier)
    if "goods_received_amount" in measures:
        per_measure["goods_received_amount"] = _query_purchase_measure("Purchase Receipt", dimension, date_from, date_to, branch_filter, supplier)
    if "outstanding_amount" in measures:
        per_measure["outstanding_amount"] = _query_purchase_outstanding(dimension, date_to, branch_filter, supplier)

    all_keys = set()
    for d in per_measure.values():
        all_keys.update(d.keys())

    return [
        {"dimension_value": key, **{m: per_measure[m].get(key, 0.0) for m in measures}}
        for key in sorted(all_keys, key=str)
    ]


# ═══════════════════════════════════════════════════════════════════════════
# Dataset: inventory (Bin for stock_qty/stock_value; days_of_stock derives
# from Bin + a 30-day Stock Ledger Entry sales window, same technique as
# tools.get_inventory_vs_sales)
# ═══════════════════════════════════════════════════════════════════════════

_INVENTORY_DIMENSIONS = {
    "branch": "w.custom_branch",
    "warehouse": "b.warehouse",
    "item_group": "i.item_group",
}
_INVENTORY_VELOCITY_DAYS = 30


def _query_inventory(measures: list[str], dimension: str | None, branch_filter, warehouse_filter) -> list[dict]:
    dim_expr = _INVENTORY_DIMENSIONS[dimension] if dimension else "'Total'"
    warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    params = {"company": _resolve_company(), "warehouse_names": tuple(warehouse_filter) if warehouse_filter else ()}

    stock_rows = frappe.db.sql(
        f"""
        SELECT {dim_expr} AS dimension_value, SUM(b.actual_qty) AS stock_qty, SUM(b.stock_value) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE w.company = %(company)s {warehouse_clause}
        GROUP BY dimension_value
        """,
        params,
        as_dict=True,
    )
    stock_by_dim = {(r.dimension_value or _("Unassigned")): {"stock_qty": flt(r.stock_qty), "stock_value": flt(r.stock_value)} for r in stock_rows}

    if "days_of_stock" not in measures:
        return [
            {"dimension_value": key, **{m: vals.get(m, 0.0) for m in measures}}
            for key, vals in stock_by_dim.items()
        ]

    # days_of_stock needs a matching sales-velocity query grouped by the same
    # dimension expression, but sales comes from Stock Ledger Entry (no Item/
    # Warehouse join columns directly usable in the same GROUP BY) - resolve via
    # a per-item/warehouse join back to the same dimension expression.
    sales_dim_expr = {"branch": "w.custom_branch", "warehouse": "sle.warehouse", "item_group": "i.item_group"}.get(dimension, "'Total'")
    date_to = getdate(today())
    date_from = add_days(date_to, -(_INVENTORY_VELOCITY_DAYS - 1))
    sales_rows = frappe.db.sql(
        f"""
        SELECT {sales_dim_expr} AS dimension_value, SUM(-sle.actual_qty) AS sales_qty
        FROM `tabStock Ledger Entry` sle
        INNER JOIN `tabWarehouse` w ON w.name = sle.warehouse
        INNER JOIN `tabItem` i ON i.name = sle.item_code
        WHERE sle.is_cancelled = 0 AND sle.company = %(company)s
          AND sle.voucher_type IN ('Sales Invoice', 'POS Invoice') AND sle.actual_qty < 0
          AND sle.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {warehouse_clause.replace('b.warehouse', 'sle.warehouse')}
        GROUP BY dimension_value
        """,
        {**params, "date_from": date_from, "date_to": date_to},
        as_dict=True,
    )
    sales_by_dim = {(r.dimension_value or _("Unassigned")): flt(r.sales_qty) for r in sales_rows}

    all_keys = set(stock_by_dim.keys()) | set(sales_by_dim.keys())
    results = []
    for key in all_keys:
        vals = dict(stock_by_dim.get(key, {"stock_qty": 0.0, "stock_value": 0.0}))
        sales_qty = sales_by_dim.get(key, 0.0)
        daily_rate = sales_qty / _INVENTORY_VELOCITY_DAYS
        vals["days_of_stock"] = round(vals.get("stock_qty", 0.0) / daily_rate, 1) if daily_rate > 0 else None
        results.append({"dimension_value": key, **{m: vals.get(m) for m in measures}})
    return results


# ═══════════════════════════════════════════════════════════════════════════
# Dataset: payables (mirrors tools_accounts.get_payables_aging's positive-
# net-balance-only filter and aging-bucket logic exactly)
# ═══════════════════════════════════════════════════════════════════════════

_PAYABLES_DIMENSIONS = {"supplier": "party", "branch": "branch", "aging_bucket": None}  # aging_bucket is Python-side


def _query_payables(dimension: str | None, date_to, branch_filter, supplier: str | None) -> list[dict]:
    branch_clause = "AND ge.branch IN %(branch_names)s" if branch_filter is not None else ""
    supplier_clause = "AND ge.party = %(supplier)s" if supplier else ""
    rows = frappe.db.sql(
        f"""
        SELECT ge.party, ge.branch, ge.debit, ge.credit, ge.posting_date
        FROM `tabGL Entry` ge
        JOIN `tabAccount` acc ON acc.name = ge.account
        WHERE ge.company = %(company)s AND ge.is_cancelled = 0 AND ge.party_type = 'Supplier'
          AND ge.posting_date <= %(date_to)s
          AND acc.is_group = 0 {branch_clause} {supplier_clause}
        """,
        {"company": _resolve_company(), "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else (), "supplier": supplier},
        as_dict=True,
    )

    if dimension == "aging_bucket":
        as_of = getdate(date_to)
        bucket_totals: dict[str, float] = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
        party_balance: dict[str, float] = {}
        for r in rows:
            party_balance[r.party] = party_balance.get(r.party, 0.0) + (flt(r.credit) - flt(r.debit))
        for r in rows:
            if party_balance.get(r.party, 0.0) <= 0:
                continue
            days = (as_of - getdate(r.posting_date)).days
            bucket = "0-30" if days <= 30 else "31-60" if days <= 60 else "61-90" if days <= 90 else "90+"
            amount = flt(r.credit) - flt(r.debit)
            bucket_totals[bucket] += amount
        return [{"dimension_value": b, "outstanding_amount": round(v, 2)} for b, v in bucket_totals.items()]

    party_totals: dict[str, float] = {}
    for r in rows:
        party_totals[r.party] = party_totals.get(r.party, 0.0) + (flt(r.credit) - flt(r.debit))
    if dimension == "supplier":
        return [{"dimension_value": k, "outstanding_amount": round(v, 2)} for k, v in party_totals.items() if v > 0]
    if dimension == "branch":
        party_branch_totals: dict[tuple[str, str], float] = {}
        for r in rows:
            key = (r.party, r.branch or _("Unassigned"))
            party_branch_totals[key] = party_branch_totals.get(key, 0.0) + (flt(r.credit) - flt(r.debit))
        branch_totals: dict[str, float] = {}
        for (_party, branch_name), value in party_branch_totals.items():
            if value > 0:
                branch_totals[branch_name] = branch_totals.get(branch_name, 0.0) + value
        return [{"dimension_value": k, "outstanding_amount": round(v, 2)} for k, v in branch_totals.items()]
    return [{
        "dimension_value": "Total",
        "outstanding_amount": round(sum(v for v in party_totals.values() if v > 0), 2),
    }]


# ═══════════════════════════════════════════════════════════════════════════
# Public tool: run_analytics_query
# ═══════════════════════════════════════════════════════════════════════════

_DATASET_MEASURES = {
    "sales": set(_SALES_MEASURES),
    "purchases": {"purchase_invoice_amount", "goods_received_amount", "outstanding_amount"},
    "inventory": {"stock_qty", "stock_value", "days_of_stock"},
    "payables": {"outstanding_amount"},
}
_DATASET_DIMENSIONS = {
    "sales": set(_SALES_DIMENSIONS),
    "purchases": set(_PURCHASE_DIMENSIONS),
    "inventory": set(_INVENTORY_DIMENSIONS),
    "payables": set(_PAYABLES_DIMENSIONS),
}


def _resolve_previous_period(date_from, date_to):
    span_days = (date_to - date_from).days
    prev_to = add_days(date_from, -1)
    prev_from = add_days(prev_to, -span_days)
    return prev_from, prev_to


def run_analytics_query(
    dataset: str,
    measures: list[str],
    dimension: str | None = None,
    branch: str | None = None,
    supplier: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str | None = None,
    sort_dir: str = "desc",
    limit: int = 50,
    compare_previous_period: bool = False,
) -> dict:
    """Governed semantic-layer query: pick a dataset (sales/purchases/inventory/
    payables), one or more approved measures, and an optional approved dimension
    to group by. Every measure/dimension name is validated against a fixed
    whitelist before any query runs - see this module's docstring for the full
    security model. Use this ONLY when no purpose-built tool already answers the
    question - always check the purpose-built tools first."""
    if dataset not in _DATASET_MEASURES:
        return {"error": f"Unknown dataset: {dataset!r}. Available: {sorted(_DATASET_MEASURES)}"}

    measures = list(measures or [])
    if not measures:
        return {"error": "At least one measure is required."}
    invalid_measures = [m for m in measures if m not in _DATASET_MEASURES[dataset]]
    if invalid_measures:
        return {"error": f"Invalid measure(s) for dataset '{dataset}': {invalid_measures}. Allowed: {sorted(_DATASET_MEASURES[dataset])}"}

    if dimension is not None and dimension not in _DATASET_DIMENSIONS[dataset]:
        return {"error": f"Invalid dimension for dataset '{dataset}': {dimension!r}. Allowed: {sorted(_DATASET_DIMENSIONS[dataset])}"}

    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch) if dataset in ("sales", "purchases", "payables") else None
    warehouse_filter = _branch_warehouses(_resolve_branch_filter(company, branch)) if dataset == "inventory" else None

    if branch_filter is not None and not branch_filter:
        return {"dataset": dataset, "measures": measures, "dimension": dimension, "rows": [], "currency": currency}
    if warehouse_filter is not None and not warehouse_filter:
        return {"dataset": dataset, "measures": measures, "dimension": dimension, "rows": [], "currency": currency}

    resolved_date_from, resolved_date_to = _get_date_range(date_from, date_to)

    def _run(d_from, d_to) -> list[dict]:
        # inventory ignores d_from/d_to entirely - it always reflects current Bin
        # state, plus a fixed trailing velocity window for days_of_stock. Not
        # date-scoped like the other 3 datasets.
        if dataset == "sales":
            return _query_sales(measures, dimension, d_from, d_to, branch_filter)
        if dataset == "purchases":
            return _query_purchases(measures, dimension, d_from, d_to, branch_filter, supplier)
        if dataset == "inventory":
            return _query_inventory(measures, dimension, branch_filter, warehouse_filter)
        if dataset == "payables":
            return _query_payables(dimension, d_to, branch_filter, supplier)
        return []

    try:
        current_rows = _run(resolved_date_from, resolved_date_to)
    except ValueError as e:
        return {"error": str(e)}

    result: dict = {
        "dataset": dataset,
        "measures": measures,
        "dimension": dimension,
        "currency": currency,
    }
    if dataset != "inventory":
        # inventory always reflects current stock (+ a fixed trailing velocity
        # window for days_of_stock) - date_from/date_to would misleadingly imply
        # the result is scoped to that range when it isn't.
        result["date_from"] = str(resolved_date_from)
        result["date_to"] = str(resolved_date_to)

    if compare_previous_period and dataset != "inventory":
        prev_from, prev_to = _resolve_previous_period(resolved_date_from, resolved_date_to)
        previous_rows = _run(prev_from, prev_to)
        current_by_key = {r["dimension_value"]: r for r in current_rows}
        previous_by_key = {r["dimension_value"]: r for r in previous_rows}
        merged = []
        for key in sorted(set(current_by_key) | set(previous_by_key), key=str):
            row = current_by_key.get(key, {})
            prev = previous_by_key.get(key, {})
            merged_row = {"dimension_value": key}
            for m in measures:
                cur_val = flt(row.get(m))
                prev_val = flt(prev.get(m))
                merged_row[m] = cur_val
                merged_row[f"{m}_previous"] = prev_val
                merged_row[f"{m}_change_pct"] = round((cur_val - prev_val) / prev_val * 100, 2) if prev_val else None
            merged.append(merged_row)
        current_rows = merged
        result["previous_date_from"] = str(prev_from)
        result["previous_date_to"] = str(prev_to)

    if sort and (sort in measures or sort == "dimension_value"):
        current_rows.sort(key=lambda r: (r.get(sort) if r.get(sort) is not None else 0), reverse=(sort_dir != "asc"))

    limit = max(1, min(cint(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    result["row_count"] = len(current_rows)
    result["rows"] = current_rows[:limit]
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Public tool: drill_down_transactions
# ═══════════════════════════════════════════════════════════════════════════

_DRILLDOWN_MAX_LIMIT = 50


def drill_down_transactions(
    dataset: str,
    branch: str | None = None,
    supplier: str | None = None,
    customer: str | None = None,
    item_code: str | None = None,
    warehouse: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> dict:
    """Returns the actual underlying documents behind a run_analytics_query
    breakdown - e.g. "show me the invoices behind this branch's sales decline".
    Same dataset names/filters as run_analytics_query. Every row includes the
    document's own `name` so the caller can reference/link to it directly."""
    if dataset not in _DATASET_MEASURES:
        return {"error": f"Unknown dataset: {dataset!r}. Available: {sorted(_DATASET_MEASURES)}"}

    company = _resolve_company()
    limit = max(1, min(cint(limit or 20), _DRILLDOWN_MAX_LIMIT))

    if dataset == "sales":
        branch_filter = _resolve_branch_filter(company, branch)
        if branch_filter is not None and not branch_filter:
            return {"dataset": dataset, "rows": []}
        resolved_date_from, resolved_date_to = _get_date_range(date_from, date_to)
        branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
        customer_clause = "AND pi.customer = %(customer)s" if customer else ""
        rows = frappe.db.sql(
            f"""
            SELECT pi.name, pi.posting_date, pi.customer, COALESCE(pi.branch, pp.branch) AS branch,
                   pi.grand_total, pi.is_return
            FROM `tabPOS Invoice` pi
            LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
            WHERE pi.docstatus = 1 AND pi.company = %(company)s
              AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
              {branch_clause} {customer_clause}
            ORDER BY pi.posting_date DESC, pi.grand_total DESC
            LIMIT %(limit)s
            """,
            {"company": company, "date_from": resolved_date_from, "date_to": resolved_date_to,
             "branch_names": tuple(branch_filter) if branch_filter else (), "customer": customer, "limit": limit},
            as_dict=True,
        )
        return {"dataset": dataset, "doctype": "POS Invoice", "rows": [
            {"name": r.name, "posting_date": str(r.posting_date), "customer": r.customer, "branch": r.branch or _("Unassigned"), "amount": flt(r.grand_total), "is_return": cint(r.is_return)}
            for r in rows
        ]}

    if dataset == "purchases":
        branch_filter = _resolve_branch_filter(company, branch)
        if branch_filter is not None and not branch_filter:
            return {"dataset": dataset, "rows": []}
        resolved_date_from, resolved_date_to = _get_date_range(date_from, date_to)
        branch_clause = "AND branch IN %(branch_names)s" if branch_filter is not None else ""
        supplier_clause = "AND supplier = %(supplier)s" if supplier else ""
        params = {"company": company, "date_from": resolved_date_from, "date_to": resolved_date_to,
                  "branch_names": tuple(branch_filter) if branch_filter else (), "supplier": supplier, "limit": limit}
        rows = frappe.db.sql(
            f"""
            (SELECT 'Purchase Invoice' AS doctype, name, posting_date, supplier, branch, base_grand_total AS amount
             FROM `tabPurchase Invoice`
             WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
               AND posting_date BETWEEN %(date_from)s AND %(date_to)s {branch_clause} {supplier_clause})
            UNION ALL
            (SELECT 'Purchase Receipt' AS doctype, name, posting_date, supplier, branch, base_grand_total AS amount
             FROM `tabPurchase Receipt`
             WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
               AND posting_date BETWEEN %(date_from)s AND %(date_to)s {branch_clause} {supplier_clause})
            ORDER BY posting_date DESC
            LIMIT %(limit)s
            """,
            params,
            as_dict=True,
        )
        return {"dataset": dataset, "rows": [
            {"doctype": r.doctype, "name": r.name, "posting_date": str(r.posting_date), "supplier": r.supplier, "branch": r.branch or _("Unassigned"), "amount": flt(r.amount)}
            for r in rows
        ]}

    if dataset == "inventory":
        branch_filter = _resolve_branch_filter(company, branch)
        warehouse_filter = _branch_warehouses(branch_filter)
        if warehouse_filter is not None and not warehouse_filter:
            return {"dataset": dataset, "rows": []}
        resolved_date_from, resolved_date_to = _get_date_range(date_from, date_to)
        warehouse_clause = "AND sle.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
        item_clause = "AND sle.item_code = %(item_code)s" if item_code else ""
        explicit_warehouse_clause = "AND sle.warehouse = %(warehouse)s" if warehouse else ""
        rows = frappe.db.sql(
            f"""
            SELECT sle.posting_date, sle.voucher_type, sle.voucher_no, sle.item_code, sle.warehouse, sle.actual_qty
            FROM `tabStock Ledger Entry` sle
            WHERE sle.is_cancelled = 0 AND sle.company = %(company)s
              AND sle.posting_date BETWEEN %(date_from)s AND %(date_to)s
              {warehouse_clause} {item_clause} {explicit_warehouse_clause}
            ORDER BY sle.posting_date DESC
            LIMIT %(limit)s
            """,
            {"company": company, "date_from": resolved_date_from, "date_to": resolved_date_to,
             "warehouse_names": tuple(warehouse_filter) if warehouse_filter else (), "item_code": item_code, "warehouse": warehouse, "limit": limit},
            as_dict=True,
        )
        return {"dataset": dataset, "rows": [
            {"posting_date": str(r.posting_date), "voucher_type": r.voucher_type, "voucher_no": r.voucher_no, "item_code": r.item_code, "warehouse": r.warehouse, "qty_change": flt(r.actual_qty)}
            for r in rows
        ]}

    if dataset == "payables":
        if not supplier:
            return {"error": "supplier is required to drill down into payables."}
        branch_filter = _resolve_branch_filter(company, branch)
        if branch_filter is not None and not branch_filter:
            return {"dataset": dataset, "rows": []}
        resolved_date_from, resolved_date_to = _get_date_range(date_from, date_to)
        branch_clause = "AND ge.branch IN %(branch_names)s" if branch_filter is not None else ""
        rows = frappe.db.sql(
            f"""
            SELECT ge.posting_date, ge.voucher_type, ge.voucher_no, ge.branch, ge.debit, ge.credit
            FROM `tabGL Entry` ge
            JOIN `tabAccount` acc ON acc.name = ge.account
            WHERE ge.company = %(company)s AND ge.is_cancelled = 0 AND ge.party_type = 'Supplier'
              AND ge.party = %(supplier)s AND acc.is_group = 0
              AND ge.posting_date BETWEEN %(date_from)s AND %(date_to)s
              {branch_clause}
            ORDER BY ge.posting_date DESC
            LIMIT %(limit)s
            """,
            {
                "company": company,
                "supplier": supplier,
                "date_from": resolved_date_from,
                "date_to": resolved_date_to,
                "branch_names": tuple(branch_filter) if branch_filter else (),
                "limit": limit,
            },
            as_dict=True,
        )
        return {"dataset": dataset, "rows": [
            {"posting_date": str(r.posting_date), "voucher_type": r.voucher_type, "voucher_no": r.voucher_no, "branch": r.branch or _("Unassigned"), "debit": flt(r.debit), "credit": flt(r.credit)}
            for r in rows
        ]}

    return {"error": f"Drill-down not implemented for dataset: {dataset!r}"}


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "run_analytics_query",
            "description": (
                "Governed analytics query over one dataset (sales/purchases/inventory/payables) with "
                "one or more approved measures, optionally grouped by an approved dimension, with "
                "optional previous-period comparison. ALWAYS prefer a purpose-built tool over this one "
                "when it fits the question - this is for combinations no fixed tool covers (e.g. "
                "'net sales by customer group this month vs last month', 'outstanding payable by "
                "branch'). Allowed per dataset - sales: measures [net_sales, gross_sales, "
                "returns_amount, txn_count, avg_basket], dimensions [branch, item_group, customer_group, month, "
                "day_of_week]. purchases: measures [purchase_invoice_amount, goods_received_amount, "
                "outstanding_amount], dimensions [branch, supplier, month] (outstanding_amount only "
                "supports branch/supplier, not month). inventory: measures [stock_qty, stock_value, "
                "days_of_stock], dimensions [branch, warehouse, item_group]. payables: measure "
                "[outstanding_amount], dimensions [supplier, branch, aging_bucket]."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "enum": ["sales", "purchases", "inventory", "payables"]},
                    "measures": {"type": "array", "items": {"type": "string"}, "description": "One or more measure names valid for the chosen dataset."},
                    "dimension": {"type": "string", "description": "Optional dimension name valid for the chosen dataset, to group results by. Omit for a single overall total."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "supplier": {"type": "string", "description": "Exact Supplier name to narrow to (purchases/payables datasets only)."},
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted. Not used by the inventory dataset (always current stock)."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "sort": {"type": "string", "description": "A measure name (or 'dimension_value') to sort rows by."},
                    "sort_dir": {"type": "string", "enum": ["asc", "desc"], "description": "Sort direction, default desc."},
                    "limit": {"type": "integer", "description": "Max rows to return, default 50, max 100."},
                    "compare_previous_period": {"type": "boolean", "description": "If true, also computes the immediately preceding equal-length period and returns <measure>_previous and <measure>_change_pct per row. Not supported for the inventory dataset (current stock has no 'previous period')."},
                },
                "required": ["dataset", "measures"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drill_down_transactions",
            "description": (
                "Returns the actual underlying documents behind a run_analytics_query result, so "
                "questions like 'show me the invoices/transactions behind this' can be answered with "
                "real, linkable documents. Same dataset names as run_analytics_query. Always call "
                "run_analytics_query first to establish the aggregate figure, then this to explain it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "enum": ["sales", "purchases", "inventory", "payables"]},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to."},
                    "supplier": {"type": "string", "description": "Exact Supplier name (purchases/payables - required for payables)."},
                    "customer": {"type": "string", "description": "Exact Customer name (sales dataset only)."},
                    "item_code": {"type": "string", "description": "Exact Item code (inventory dataset only)."},
                    "warehouse": {"type": "string", "description": "Exact Warehouse name (inventory dataset only)."},
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "limit": {"type": "integer", "description": "Max documents to return, default 20, max 50."},
                },
                "required": ["dataset"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "run_analytics_query": run_analytics_query,
    "drill_down_transactions": drill_down_transactions,
}
