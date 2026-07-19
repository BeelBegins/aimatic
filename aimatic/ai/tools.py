"""Read-only aggregate-query tools exposed to the Nemotron chat agent (api.py:ask).

Every tool here is deliberately cheap (SUM/GROUP BY aggregates, capped limits, no
per-item ledger replay, no N+1 per-entity queries) and Company/Branch-permission
scoped the same way sales_dashboard/api.py and vendor_performance/api.py already
are - a branch-restricted user asking the assistant must not see numbers outside
their visible branches. None of these ever write to the database.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

_ALLOWED_ORDER = {"best", "worst"}
_ALLOWED_DIRECTION = {"overstock", "understock"}


def _resolve_company(company: str | None = None) -> str:
    company = company or frappe.defaults.get_user_default("Company") or frappe.defaults.get_default("company")
    if not company:
        frappe.throw(_("No default Company is configured for this user."))
    return company


def _resolve_branch_filter(company: str, branch: str | None) -> list[str] | None:
    """None = no filter (full company, including branch-less rows). A list =
    restrict to exactly these branches - either the caller's explicit choice, or
    the current user's own Branch User Permission set when it's a strict subset of
    the company's branches. Mirrors sales_dashboard/api.py's helper of the same
    purpose so chat answers stay consistent with what the dashboards already show."""
    if branch:
        if not frappe.has_permission("Branch", ptype="read", doc=branch):
            frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)
        return [branch]
    visible = frappe.get_all("Branch", filters={"company": company}, pluck="name")
    total_count = frappe.db.count("Branch", {"company": company})
    if len(visible) < total_count:
        return visible
    return None


def _branch_warehouses(branch_filter: list[str] | None) -> list[str] | None:
    """Expands a branch filter to its warehouse list via Warehouse.custom_branch,
    for scoping Bin/Stock Ledger Entry queries (which carry warehouse, not branch).
    None means unfiltered; a (possibly empty) list means restrict to exactly those
    warehouses."""
    if branch_filter is None:
        return None
    if not branch_filter:
        return []
    return frappe.get_all("Warehouse", filters={"custom_branch": ["in", branch_filter], "disabled": 0}, pluck="name")


def _get_date_range(date_from: str | None, date_to: str | None):
    date_to = getdate(date_to) if date_to else getdate(today())
    date_from = getdate(date_from) if date_from else date_to
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def get_sales_overview(date_from: str | None = None, date_to: str | None = None, branch: str | None = None) -> dict:
    """Net/gross sales, returns, transaction count, average basket, and a
    per-branch breakdown for the given date range (defaults to today)."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "net_sales": 0, "gross_sales": 0, "returns_amount": 0, "txn_count": 0, "average_basket": 0, "branch_breakdown": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    params = {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()}

    row = frappe.db.sql(
        f"""
        SELECT
            COALESCE(SUM(pi.grand_total), 0) AS net_sales,
            COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN pi.grand_total END), 0) AS gross_sales,
            COALESCE(SUM(CASE WHEN pi.is_return = 1 THEN -pi.grand_total END), 0) AS returns_amount,
            COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN 1 ELSE 0 END), 0) AS sales_txn_count,
            COUNT(*) AS txn_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
        params,
        as_dict=True,
    )[0]

    breakdown_rows = frappe.db.sql(
        f"""
        SELECT COALESCE(pi.branch, pp.branch) AS branch, COALESCE(SUM(pi.grand_total), 0) AS net_sales
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY COALESCE(pi.branch, pp.branch)
        ORDER BY net_sales DESC
        """,
        params,
        as_dict=True,
    )

    sales_txn_count = cint(row.sales_txn_count)
    gross_sales = flt(row.gross_sales)
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "net_sales": flt(row.net_sales),
        "gross_sales": gross_sales,
        "returns_amount": flt(row.returns_amount),
        "txn_count": cint(row.txn_count),
        "average_basket": (gross_sales / sales_txn_count) if sales_txn_count else 0,
        "branch_breakdown": [{"branch": r.branch or _("Unassigned"), "net_sales": flt(r.net_sales)} for r in breakdown_rows],
    }


def get_purchase_overview(date_from: str | None = None, date_to: str | None = None, branch: str | None = None, supplier: str | None = None) -> dict:
    """Purchase amount/qty/doc count (Purchase Invoice), goods-received amount/qty
    (Purchase Receipt), and total outstanding payable - sitewide, or narrowed to
    one supplier/branch if given."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "purchase_amount": 0, "purchase_doc_count": 0, "receipt_amount": 0, "outstanding_amount": 0}

    branch_clause = "AND branch IN %(branch_names)s" if branch_filter is not None else ""
    supplier_clause = "AND supplier = %(supplier)s" if supplier else ""
    params = {
        "company": company,
        "date_from": date_from,
        "date_to": date_to,
        "branch_names": tuple(branch_filter) if branch_filter else (),
        "supplier": supplier,
    }

    invoice_row = frappe.db.sql(
        f"""
        SELECT COUNT(*) AS doc_count, COALESCE(SUM(base_grand_total), 0) AS purchase_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause} {supplier_clause}
        """,
        params,
        as_dict=True,
    )[0]

    receipt_row = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(base_grand_total), 0) AS receipt_amount
        FROM `tabPurchase Receipt`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause} {supplier_clause}
        """,
        params,
        as_dict=True,
    )[0]

    # Sourced from GL Entry (party_type='Supplier'), NOT Purchase Invoice.outstanding_amount -
    # same real bug as get_outstanding_payables_overview (see that function's docstring):
    # a site whose supplier debt is carried mostly via legacy Journal Entries (e.g. siezal's
    # iPOS migration opening balances) shows PKR 0 outstanding here if only Purchase Invoice
    # rows are summed, even though the real Creditors-account balance is nonzero. GL Entry is
    # the only source that captures Purchase Invoice, Journal Entry, and Payment Entry
    # postings against a supplier uniformly.
    gl_branch_clause = "AND branch IN %(branch_names)s" if branch_filter is not None else ""
    gl_supplier_clause = "AND party = %(supplier)s" if supplier else ""
    outstanding_row = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(balance), 0) AS outstanding_amount
        FROM (
            SELECT party, SUM(credit - debit) AS balance
            FROM `tabGL Entry`
            WHERE party_type = 'Supplier' AND is_cancelled = 0 AND company = %(company)s
              {gl_branch_clause} {gl_supplier_clause}
            GROUP BY party
            HAVING SUM(credit - debit) > 0
        ) per_supplier
        """,
        params,
        as_dict=True,
    )[0]

    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "supplier": supplier,
        "purchase_amount": flt(invoice_row.purchase_amount),
        "purchase_doc_count": cint(invoice_row.doc_count),
        "receipt_amount": flt(receipt_row.receipt_amount),
        "outstanding_amount": flt(outstanding_row.outstanding_amount),
    }


def rank_vendors(date_from: str | None = None, date_to: str | None = None, order: str = "best", limit: int = 10) -> dict:
    """Ranks suppliers with purchase activity in the window by gross margin % on
    their linked items (via each item's Item Default.default_supplier - a lighter
    per-item attribution than vendor_performance's exhaustive 4-source lookup,
    appropriate here since this ranks *all* vendors at once rather than drilling
    into one). Returns raw metrics per vendor, not just a single score, so the
    model's explanation is grounded in visible numbers rather than an opaque rank."""
    order = order if order in _ALLOWED_ORDER else "best"
    limit = max(1, min(cint(limit or 10), 30))
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    # Candidate suppliers come from Purchase Invoice AND Purchase Receipt, not Invoice
    # alone - a real bug found live on siezal (2026-07-19): siezal has zero submitted
    # Purchase Invoices (its purchase activity is recorded via Purchase Receipt), so an
    # Invoice-only candidate query always returned `vendors: []`, permanently breaking
    # this tool on that site regardless of what the model asked for. Invoice figures win
    # when a supplier has both (avoids double-counting a receipt that was later
    # invoiced); Receipt figures are the fallback for suppliers with receipt-only
    # activity in the window.
    invoice_rows = frappe.db.sql(
        """
        SELECT supplier, COUNT(*) AS doc_count, COALESCE(SUM(base_grand_total), 0) AS purchase_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
        GROUP BY supplier
        """,
        {"company": company, "date_from": date_from, "date_to": date_to},
        as_dict=True,
    )
    receipt_rows = frappe.db.sql(
        """
        SELECT supplier, COUNT(*) AS doc_count, COALESCE(SUM(base_grand_total), 0) AS purchase_amount
        FROM `tabPurchase Receipt`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
        GROUP BY supplier
        """,
        {"company": company, "date_from": date_from, "date_to": date_to},
        as_dict=True,
    )
    purchase_by_supplier = {r.supplier: r for r in receipt_rows}
    purchase_by_supplier.update({r.supplier: r for r in invoice_rows})
    purchase_rows = list(purchase_by_supplier.values())
    if not purchase_rows:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "order": order, "vendors": []}

    # Sourced from GL Entry, NOT Purchase Invoice.outstanding_amount - same class of
    # bug already fixed for get_outstanding_payables_overview/get_purchase_overview
    # (see their docstrings): a supplier's real debt can be carried via Journal Entry
    # (e.g. legacy iPOS opening balances) rather than a Purchase Invoice.
    outstanding_rows = frappe.db.sql(
        """
        SELECT party AS supplier, balance AS outstanding_amount
        FROM (
            SELECT party, SUM(credit - debit) AS balance
            FROM `tabGL Entry`
            WHERE party_type = 'Supplier' AND is_cancelled = 0 AND company = %(company)s
            GROUP BY party
            HAVING SUM(credit - debit) > 0
        ) per_supplier
        """,
        {"company": company},
        as_dict=True,
    )
    outstanding_by_supplier = {r.supplier: flt(r.outstanding_amount) for r in outstanding_rows}

    # Item -> canonical supplier for this company, used to attribute sales/COGS.
    item_supplier_rows = frappe.db.sql(
        """
        SELECT parent AS item_code, default_supplier AS supplier
        FROM `tabItem Default`
        WHERE default_supplier IS NOT NULL AND default_supplier != ''
          AND (company = %(company)s OR company IS NULL OR company = '')
        """,
        {"company": company},
        as_dict=True,
    )
    supplier_by_item = {r.item_code: r.supplier for r in item_supplier_rows}

    sales_by_supplier: dict[str, float] = {}
    cogs_by_supplier: dict[str, float] = {}
    if supplier_by_item:
        item_codes = tuple(supplier_by_item.keys())
        # POS Invoice only, deliberately not UNIONed with Sales Invoice Item: every
        # closed POS shift consolidates its POS Invoices into a *new*, separate
        # Sales Invoice with its own mirrored item rows (see
        # POSInvoice.on_submit -> consolidate_pos_invoices in CLAUDE.md's offline_pos
        # notes) while the original POS Invoices stay docstatus=1, not cancelled -
        # confirmed on szl (item 0000000000002: identical 31 rows / 193,306.44 in
        # both tables for the same sales). Unioning the two double-counts revenue
        # for every sale that's gone through a shift close. POS Settings.invoice_type
        # is "POS Invoice" on all sites, so POS Invoice alone is the complete,
        # correct source of POS sales revenue - matching get_sales_overview's own
        # POS-Invoice-only approach.
        sales_rows = frappe.db.sql(
            """
            SELECT item_code, SUM(base_net_amount) AS sales_amount
            FROM `tabPOS Invoice Item` pii
            INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
              AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s AND pii.item_code IN %(item_codes)s
            GROUP BY item_code
            """,
            {"company": company, "date_from": date_from, "date_to": date_to, "item_codes": item_codes},
            as_dict=True,
        )
        cogs_rows = frappe.db.sql(
            """
            SELECT item_code, SUM(-stock_value_difference) AS cogs_amount
            FROM `tabStock Ledger Entry`
            WHERE is_cancelled = 0 AND company = %(company)s AND item_code IN %(item_codes)s
              AND voucher_type IN ('Sales Invoice', 'POS Invoice') AND actual_qty < 0
              AND posting_date BETWEEN %(date_from)s AND %(date_to)s
            GROUP BY item_code
            """,
            {"company": company, "date_from": date_from, "date_to": date_to, "item_codes": item_codes},
            as_dict=True,
        )
        for r in sales_rows:
            supplier = supplier_by_item.get(r.item_code)
            if supplier:
                sales_by_supplier[supplier] = sales_by_supplier.get(supplier, 0) + flt(r.sales_amount)
        for r in cogs_rows:
            supplier = supplier_by_item.get(r.item_code)
            if supplier:
                cogs_by_supplier[supplier] = cogs_by_supplier.get(supplier, 0) + flt(r.cogs_amount)

    vendors = []
    for row in purchase_rows:
        supplier = row.supplier
        sales_amount = sales_by_supplier.get(supplier, 0)
        cogs_amount = cogs_by_supplier.get(supplier, 0)
        gross_margin_pct = ((sales_amount - cogs_amount) / sales_amount * 100) if sales_amount else None
        vendors.append(
            {
                "supplier": supplier,
                "purchase_amount": flt(row.purchase_amount),
                "purchase_doc_count": cint(row.doc_count),
                "sales_amount": flt(sales_amount),
                "gross_margin_pct": round(gross_margin_pct, 2) if gross_margin_pct is not None else None,
                "outstanding_amount": outstanding_by_supplier.get(supplier, 0),
            }
        )

    # Vendors with no attributable sales data (gross_margin_pct is None) sort last
    # regardless of direction - there's no margin signal to call them best or worst on.
    def sort_key(v):
        has_margin = v["gross_margin_pct"] is not None
        margin = v["gross_margin_pct"] if has_margin else 0
        if order == "best":
            return (not has_margin, -margin, -v["purchase_amount"])
        return (not has_margin, margin, -v["outstanding_amount"])

    vendors.sort(key=sort_key)
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "order": order,
        "vendors": vendors[:limit],
    }


def get_inventory_vs_sales(direction: str = "overstock", lookback_days: int = 30, branch: str | None = None, limit: int = 20) -> dict:
    """Per-item current stock vs. trailing sales velocity, expressed as days-of-stock
    (stock_qty / average daily sales). "overstock" surfaces slow movers (highest
    days-of-stock, including dead stock with zero sales but nonzero stock).
    "understock" surfaces items still selling with the lowest days-of-stock,
    including zero-stock stockout risk."""
    direction = direction if direction in _ALLOWED_DIRECTION else "overstock"
    lookback_days = max(1, min(cint(lookback_days or 30), 180))
    limit = max(1, min(cint(limit or 20), 50))
    company = _resolve_company()
    branch_filter = _resolve_branch_filter(company, branch)
    warehouse_filter = _branch_warehouses(branch_filter)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if warehouse_filter is not None and not warehouse_filter:
        return {"company": company, "currency": currency, "direction": direction, "lookback_days": lookback_days, "items": []}

    date_to = getdate(today())
    date_from = add_days(date_to, -(lookback_days - 1))
    warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    sle_warehouse_clause = "AND warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    params = {"company": company, "date_from": date_from, "date_to": date_to, "warehouse_names": tuple(warehouse_filter) if warehouse_filter else ()}

    stock_rows = frappe.db.sql(
        f"""
        SELECT b.item_code, i.item_name, SUM(b.actual_qty) AS stock_qty, SUM(b.stock_value) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE w.company = %(company)s {warehouse_clause}
        GROUP BY b.item_code, i.item_name
        HAVING ABS(SUM(b.actual_qty)) > 0.0001 OR ABS(SUM(b.stock_value)) > 0.0001
        """,
        params,
        as_dict=True,
    )
    sales_rows = frappe.db.sql(
        f"""
        SELECT item_code, SUM(-actual_qty) AS sales_qty
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0 AND company = %(company)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice') AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {sle_warehouse_clause}
        GROUP BY item_code
        """,
        params,
        as_dict=True,
    )

    stock_by_item = {r.item_code: r for r in stock_rows}
    sales_by_item = {r.item_code: flt(r.sales_qty) for r in sales_rows}
    item_names = {r.item_code: r.item_name for r in stock_rows}
    if not item_names:
        missing = set(sales_by_item.keys())
        if missing:
            for r in frappe.get_all("Item", filters={"name": ["in", list(missing)]}, fields=["name", "item_name"]):
                item_names[r.name] = r.item_name

    all_item_codes = set(stock_by_item.keys()) | set(sales_by_item.keys())
    results = []
    for item_code in all_item_codes:
        stock = stock_by_item.get(item_code)
        stock_qty = flt(stock.stock_qty) if stock else 0
        stock_value = flt(stock.stock_value) if stock else 0
        sales_qty = sales_by_item.get(item_code, 0)
        daily_rate = sales_qty / lookback_days if lookback_days else 0
        days_of_stock = (stock_qty / daily_rate) if daily_rate > 0 else None

        if direction == "overstock":
            if stock_qty <= 0:
                continue
        else:  # understock
            if sales_qty <= 0:
                continue

        results.append(
            {
                "item_code": item_code,
                "item_name": item_names.get(item_code) or item_code,
                "stock_qty": stock_qty,
                "stock_value": stock_value,
                "sales_qty_in_window": sales_qty,
                "days_of_stock": round(days_of_stock, 1) if days_of_stock is not None else None,
            }
        )

    if direction == "overstock":
        # No sales at all (days_of_stock is None) is the worst overstock case -
        # dead stock - so it sorts first, ahead of any finite (however large) ratio.
        results.sort(key=lambda r: (r["days_of_stock"] is not None, -(r["days_of_stock"] or 0)))
    else:
        results.sort(key=lambda r: (r["days_of_stock"] if r["days_of_stock"] is not None else 0))

    return {
        "company": company,
        "currency": currency,
        "direction": direction,
        "lookback_days": lookback_days,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "items": results[:limit],
    }


def get_branch_comparison(date_from: str | None = None, date_to: str | None = None) -> dict:
    """Net sales per branch for a date range - lets the model answer "compare
    branches" questions directly instead of only a single company-wide total.
    Same query shape as sales_dashboard/api.py's _get_branch_comparison."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, None)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "branches": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    rows = frappe.db.sql(
        f"""
        SELECT COALESCE(pi.branch, pp.branch) AS branch, COALESCE(SUM(pi.grand_total), 0) AS net_sales
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY COALESCE(pi.branch, pp.branch)
        ORDER BY net_sales DESC
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "branches": [{"branch": r.branch or _("Unassigned"), "net_sales": flt(r.net_sales)} for r in rows],
    }


def get_payment_mode_split(date_from: str | None = None, date_to: str | None = None, branch: str | None = None) -> dict:
    """Payment-mode breakdown (Cash/Card/etc.) for a date range. Same query as
    sales_dashboard/api.py's _get_payment_split."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "payment_split": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    rows = frappe.db.sql(
        f"""
        SELECT pay.mode_of_payment AS mode_of_payment, COALESCE(SUM(pay.amount), 0) AS amount
        FROM `tabSales Invoice Payment` pay
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pay.parent AND pay.parenttype = 'POS Invoice'
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY pay.mode_of_payment
        ORDER BY amount DESC
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "payment_split": [{"mode_of_payment": r.mode_of_payment, "amount": flt(r.amount)} for r in rows],
    }


def get_returns_overview(date_from: str | None = None, date_to: str | None = None, branch: str | None = None) -> dict:
    """Total returns amount/count plus a per-branch breakdown for a date range."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "returns_amount": 0, "returns_count": 0, "branch_breakdown": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    params = {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()}

    total_row = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(-pi.grand_total), 0) AS returns_amount, COUNT(*) AS returns_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s AND pi.is_return = 1
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
        params,
        as_dict=True,
    )[0]

    branch_rows = frappe.db.sql(
        f"""
        SELECT COALESCE(pi.branch, pp.branch) AS branch, COALESCE(SUM(-pi.grand_total), 0) AS returns_amount, COUNT(*) AS returns_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s AND pi.is_return = 1
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY COALESCE(pi.branch, pp.branch)
        ORDER BY returns_amount DESC
        """,
        params,
        as_dict=True,
    )

    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "returns_amount": flt(total_row.returns_amount),
        "returns_count": cint(total_row.returns_count),
        "branch_breakdown": [{"branch": r.branch or _("Unassigned"), "returns_amount": flt(r.returns_amount), "returns_count": cint(r.returns_count)} for r in branch_rows],
    }


def get_active_shifts() -> dict:
    """Currently-open POS shifts (POS Opening Entry, docstatus=1, status=Open) with
    live running totals - mirrors ERPNext's own pos_closing_entry.build_invoice_query
    technique (same as sales_dashboard/api.py's _get_open_shifts) so a shift that
    hasn't closed yet still shows an accurate running total."""
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, None)

    openings = frappe.get_all(
        "POS Opening Entry",
        filters={"company": company, "docstatus": 1, "status": "Open"},
        fields=["name", "pos_profile", "user", "period_start_date"],
        order_by="period_start_date desc",
    )
    if not openings:
        return {"company": company, "currency": currency, "active_shifts": []}

    profile_names = list({o.pos_profile for o in openings})
    profile_branch = {
        p.name: p.branch
        for p in frappe.get_all("POS Profile", filters={"name": ["in", profile_names]}, fields=["name", "branch"])
    }
    if branch_filter is not None:
        allowed = set(branch_filter)
        openings = [o for o in openings if profile_branch.get(o.pos_profile) in allowed]
        if not openings:
            return {"company": company, "currency": currency, "active_shifts": []}

    shifts = []
    for opening in openings:
        row = frappe.db.sql(
            """
            SELECT COALESCE(SUM(grand_total), 0) AS running_total, COUNT(*) AS txn_count
            FROM `tabPOS Invoice`
            WHERE docstatus = 1 AND is_pos = 1 AND owner = %(user)s AND pos_profile = %(pos_profile)s
              AND TIMESTAMP(posting_date, posting_time) BETWEEN %(start)s AND %(now)s
            """,
            {"user": opening.user, "pos_profile": opening.pos_profile, "start": opening.period_start_date, "now": now_datetime()},
            as_dict=True,
        )[0]
        shifts.append(
            {
                "pos_profile": opening.pos_profile,
                "branch": profile_branch.get(opening.pos_profile) or _("Unassigned"),
                "user": opening.user,
                "opened_at": str(opening.period_start_date),
                "running_total": flt(row.running_total),
                "txn_count": cint(row.txn_count),
            }
        )
    return {"company": company, "currency": currency, "active_shifts": shifts}


def get_top_selling_items(date_from: str | None = None, date_to: str | None = None, branch: str | None = None, order_by: str = "revenue", limit: int = 10) -> dict:
    """Top N items by sales revenue or quantity for a date range - a new sitewide
    aggregate (not covered by any existing report). POS Invoice only, deliberately
    not unioned with Sales Invoice Item: every closed POS shift consolidates its POS
    Invoices into a *new*, separate Sales Invoice with its own mirrored item rows,
    while the originals stay docstatus=1 (confirmed on szl - unioning the two
    double-counts revenue for every sale that's gone through a shift close).
    POS Settings.invoice_type is "POS Invoice" on all sites, so POS Invoice alone is
    the complete, correct source - matching get_sales_overview's own approach."""
    order_by = order_by if order_by in ("revenue", "quantity") else "revenue"
    limit = max(1, min(cint(limit or 10), 50))
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "order_by": order_by, "items": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    order_column = "sales_amount" if order_by == "revenue" else "sales_qty"
    rows = frappe.db.sql(
        f"""
        SELECT pii.item_code, i.item_name, SUM(pii.stock_qty) AS sales_qty, SUM(pii.base_net_amount) AS sales_amount
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        INNER JOIN `tabItem` i ON i.name = pii.item_code
        WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY pii.item_code, i.item_name
        ORDER BY {order_column} DESC
        LIMIT %(limit)s
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else (), "limit": limit},
        as_dict=True,
    )
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "order_by": order_by,
        "items": [{"item_code": r.item_code, "item_name": r.item_name, "sales_qty": flt(r.sales_qty), "sales_amount": flt(r.sales_amount)} for r in rows],
    }


def get_outstanding_payables_overview(limit: int = 10) -> dict:
    """Total outstanding payable plus the top N suppliers by amount owed.

    Sourced from GL Entry (party_type='Supplier'), NOT Purchase Invoice.outstanding_amount -
    a real bug found live on siezal (2026-07-18): Purchase Invoice-only sourcing showed
    PKR 0.00 total payable while the real Creditors-account balance was ~PKR 35.5M, because
    siezal's supplier debt is carried almost entirely via legacy per-row opening-balance
    Journal Entries from the iPOS migration (see supplierimport.md / CLAUDE.md), not
    Purchase Invoices - siezal had zero submitted Purchase Invoices at the time. GL Entry
    is the only source that captures Purchase Invoice, Journal Entry, and Payment Entry
    postings against a supplier uniformly, matching how ERPNext's own Accounts Payable
    report computes real outstanding balance."""
    limit = max(1, min(cint(limit or 10), 30))
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")

    per_supplier_balance = frappe.db.sql(
        """
        SELECT party AS supplier, SUM(credit - debit) AS outstanding_amount, COUNT(DISTINCT voucher_no) AS voucher_count
        FROM `tabGL Entry`
        WHERE party_type = 'Supplier' AND is_cancelled = 0 AND company = %(company)s
        GROUP BY party
        HAVING SUM(credit - debit) > 0
        ORDER BY outstanding_amount DESC
        """,
        {"company": company},
        as_dict=True,
    )
    total_outstanding_amount = sum(flt(r.outstanding_amount) for r in per_supplier_balance)
    supplier_rows = per_supplier_balance[:limit]

    return {
        "company": company,
        "currency": currency,
        "total_outstanding_amount": flt(total_outstanding_amount),
        "total_outstanding_invoice_count": len(per_supplier_balance),
        "top_suppliers": [{"supplier": r.supplier, "outstanding_amount": flt(r.outstanding_amount), "invoice_count": cint(r.voucher_count)} for r in supplier_rows],
    }


def get_gross_margin_overview(date_from: str | None = None, date_to: str | None = None, branch: str | None = None) -> dict:
    """Company-wide gross margin: sales revenue (POS Invoice grand_total, same as
    get_sales_overview) vs. cost-basis COGS (Stock Ledger Entry valuation, same
    filter vendor_performance/api.py's _get_cogs_summary uses but across all items,
    not one supplier's linked SKUs)."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    warehouse_filter = _branch_warehouses(branch_filter)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "sales_amount": 0, "cogs_amount": 0, "gross_margin_amount": 0, "gross_margin_pct": 0}

    sales_branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    sales_row = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(pi.grand_total), 0) AS sales_amount
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.is_return = 0 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {sales_branch_clause}
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )[0]

    warehouse_clause = "AND warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    cogs_row = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(-stock_value_difference), 0) AS cogs_amount
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0 AND company = %(company)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice') AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {warehouse_clause}
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "warehouse_names": tuple(warehouse_filter) if warehouse_filter else ()},
        as_dict=True,
    )[0]

    sales_amount = flt(sales_row.sales_amount)
    cogs_amount = flt(cogs_row.cogs_amount)
    gross_margin_amount = sales_amount - cogs_amount
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "sales_amount": sales_amount,
        "cogs_amount": cogs_amount,
        "gross_margin_amount": gross_margin_amount,
        "gross_margin_pct": round(gross_margin_amount / sales_amount * 100, 2) if sales_amount else 0,
    }


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_sales_overview",
            "description": "Get net/gross sales, returns, transaction count, average basket size, and a per-branch breakdown for a date range. Use for any question about sales, revenue, or transaction volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_purchase_overview",
            "description": "Get purchase amount, goods-received amount, and outstanding payable for a date range, optionally for one supplier. Use for any question about purchases, spending on suppliers, or payables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "supplier": {"type": "string", "description": "Exact Supplier name to narrow to one vendor. Omit for all suppliers."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_vendors",
            "description": "Rank suppliers by gross margin % on their linked items' sales, purchase volume, and outstanding payable, for a date range. Use for any question asking which vendors/suppliers are good, bad, best, or worst.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "order": {"type": "string", "enum": ["best", "worst"], "description": "\"best\" for top vendors by margin, \"worst\" for lowest margin / highest outstanding."},
                    "limit": {"type": "integer", "description": "Max vendors to return, default 10, max 30."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_inventory_vs_sales",
            "description": "Compare current stock levels against recent sales velocity, expressed as days-of-stock. Use for any question about overstocked/slow-moving items, understocked/stockout-risk items, or inventory vs. sales.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["overstock", "understock"], "description": "\"overstock\" for slow movers with too much stock relative to sales, \"understock\" for fast movers at risk of running out."},
                    "lookback_days": {"type": "integer", "description": "Trailing window in days to measure sales velocity, default 30, max 180."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "limit": {"type": "integer", "description": "Max items to return, default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_branch_comparison",
            "description": "Compare net sales across all branches for a date range. Use for any question comparing branches/stores against each other.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_mode_split",
            "description": "Get the breakdown of sales by payment mode (Cash, Card, etc.) for a date range. Use for any question about how customers paid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_returns_overview",
            "description": "Get total returns/refunds amount and count for a date range, with a per-branch breakdown. Use for any question about returns or refunds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_shifts",
            "description": "Get currently open POS shifts and their live running sales totals. Use for any question about shifts currently open, active cashiers, or live/in-progress sales.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_selling_items",
            "description": "Get the top-selling items by revenue or quantity for a date range. Use for any question about best-selling, top, or most popular products.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "order_by": {"type": "string", "enum": ["revenue", "quantity"], "description": "Rank by sales revenue or by quantity sold, default revenue."},
                    "limit": {"type": "integer", "description": "Max items to return, default 10, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_outstanding_payables_overview",
            "description": "Get total outstanding payable to suppliers and the top suppliers by amount owed. Use for any question about how much is owed to vendors/suppliers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max suppliers to return, default 10, max 30."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_gross_margin_overview",
            "description": "Get company-wide sales revenue, cost of goods sold, gross margin amount, and gross margin percentage for a date range. Use for any question about overall profitability or margin.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
]

TOOL_DISPATCH = {
    "get_sales_overview": get_sales_overview,
    "get_purchase_overview": get_purchase_overview,
    "rank_vendors": rank_vendors,
    "get_inventory_vs_sales": get_inventory_vs_sales,
    "get_branch_comparison": get_branch_comparison,
    "get_payment_mode_split": get_payment_mode_split,
    "get_returns_overview": get_returns_overview,
    "get_active_shifts": get_active_shifts,
    "get_top_selling_items": get_top_selling_items,
    "get_outstanding_payables_overview": get_outstanding_payables_overview,
    "get_gross_margin_overview": get_gross_margin_overview,
}
