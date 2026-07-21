"""Phase 4a: expanded read-only aggregate-query tools for the Nemotron chat agent
(api.py:ask), added to cover sales-trend/purchasing/inventory/customer/operational
categories the original 25 tools didn't reach. Same conventions as tools.py/
tools_extended.py/tools_accounts.py: cheap SUM/GROUP BY aggregates, capped limits,
Company/Branch-permission scoped via tools._resolve_company/_resolve_branch_filter/
_branch_warehouses, no per-row replay, nothing ever written.
"""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

from aimatic.ai.tools import (
    _resolve_company,
    _resolve_branch_filter,
    _branch_warehouses,
    _get_date_range,
)

_ALLOWED_GRANULARITY = {"day", "week", "month"}
_WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# ═══════════════════════════════════════════════════════════════════════════
# Sales intelligence
# ═══════════════════════════════════════════════════════════════════════════

def get_sales_trend(date_from: str | None = None, date_to: str | None = None, granularity: str = "day", branch: str | None = None) -> dict:
    """Net sales bucketed by day/week/month over a date range. Unlike most tools in
    this module, defaults to a trailing 30-day window (not "today only") when no
    dates are given, since a single day has no trend to show."""
    granularity = granularity if granularity in _ALLOWED_GRANULARITY else "day"
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)

    if date_from or date_to:
        date_from, date_to = _get_date_range(date_from, date_to)
    else:
        date_to = getdate(today())
        date_from = add_days(date_to, -29)

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "granularity": granularity, "trend": []}

    bucket_expr = {
        "day": "pi.posting_date",
        "week": "DATE_SUB(pi.posting_date, INTERVAL WEEKDAY(pi.posting_date) DAY)",
        "month": "DATE_FORMAT(pi.posting_date, '%Y-%m-01')",
    }[granularity]
    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""

    rows = frappe.db.sql(
        f"""
        SELECT {bucket_expr} AS bucket, COALESCE(SUM(pi.grand_total), 0) AS net_sales, COUNT(*) AS txn_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "granularity": granularity,
        "trend": [{"bucket": str(r.bucket), "net_sales": flt(r.net_sales), "txn_count": cint(r.txn_count)} for r in rows],
    }


def get_hourly_sales_pattern(date_from: str | None = None, date_to: str | None = None, branch: str | None = None) -> dict:
    """Net sales broken down by hour-of-day (0-23) and by weekday, over a date range.
    Defaults to a trailing 30-day window (not "today only") - a single day has no
    weekday pattern to show."""
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)

    if date_from or date_to:
        date_from, date_to = _get_date_range(date_from, date_to)
    else:
        date_to = getdate(today())
        date_from = add_days(date_to, -29)

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "by_hour": [], "by_weekday": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    params = {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()}

    hour_rows = frappe.db.sql(
        f"""
        SELECT HOUR(pi.posting_time) AS hour, COALESCE(SUM(pi.grand_total), 0) AS net_sales, COUNT(*) AS txn_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s AND pi.posting_time IS NOT NULL
          {branch_clause}
        GROUP BY hour
        ORDER BY hour ASC
        """,
        params,
        as_dict=True,
    )
    weekday_rows = frappe.db.sql(
        f"""
        SELECT WEEKDAY(pi.posting_date) AS weekday, COALESCE(SUM(pi.grand_total), 0) AS net_sales, COUNT(*) AS txn_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY weekday
        ORDER BY weekday ASC
        """,
        params,
        as_dict=True,
    )
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "by_hour": [{"hour": cint(r.hour), "net_sales": flt(r.net_sales), "txn_count": cint(r.txn_count)} for r in hour_rows],
        "by_weekday": [{"weekday": _WEEKDAY_NAMES[cint(r.weekday)], "net_sales": flt(r.net_sales), "txn_count": cint(r.txn_count)} for r in weekday_rows],
    }


def get_discount_overview(date_from: str | None = None, date_to: str | None = None, branch: str | None = None) -> dict:
    """Total discount given on POS sales in a date range - header-level additional
    discount (POS Invoice.discount_amount) plus item-level discount (POS Invoice
    Item.discount_amount * qty), with a per-branch breakdown. effective_discount_pct
    is discount / (discount + net_sales), an approximation of "discount as % of
    what the sale would have been at list price" - not an exact reconstruction of
    each line's original price_list_rate, but a reasonable leakage signal."""
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "total_discount_amount": 0, "net_sales": 0, "effective_discount_pct": 0, "branch_breakdown": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    params = {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()}

    header_rows = frappe.db.sql(
        f"""
        SELECT COALESCE(pi.branch, pp.branch) AS branch,
               COALESCE(SUM(pi.discount_amount), 0) AS header_discount,
               COALESCE(SUM(pi.grand_total), 0) AS net_sales
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY COALESCE(pi.branch, pp.branch)
        """,
        params,
        as_dict=True,
    )
    item_rows = frappe.db.sql(
        f"""
        SELECT COALESCE(pi.branch, pp.branch) AS branch, COALESCE(SUM(pii.discount_amount * pii.qty), 0) AS item_discount
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY COALESCE(pi.branch, pp.branch)
        """,
        params,
        as_dict=True,
    )
    item_discount_by_branch = {r.branch: flt(r.item_discount) for r in item_rows}

    breakdown = []
    total_discount = 0.0
    total_net_sales = 0.0
    for row in header_rows:
        branch_name = row.branch or _("Unassigned")
        discount = flt(row.header_discount) + item_discount_by_branch.get(row.branch, 0.0)
        net_sales = flt(row.net_sales)
        total_discount += discount
        total_net_sales += net_sales
        breakdown.append({"branch": branch_name, "discount_amount": discount, "net_sales": net_sales})

    breakdown.sort(key=lambda r: -r["discount_amount"])
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "total_discount_amount": round(total_discount, 2),
        "net_sales": round(total_net_sales, 2),
        "effective_discount_pct": round(total_discount / (total_discount + total_net_sales) * 100, 2) if (total_discount + total_net_sales) else 0,
        "branch_breakdown": breakdown,
    }


def get_sales_by_item_group(date_from: str | None = None, date_to: str | None = None, branch: str | None = None, limit: int = 20) -> dict:
    """Revenue contribution by item group (category) for a date range - "what's
    selling" one level up from get_top_selling_items' per-item view."""
    limit = max(1, min(cint(limit or 20), 50))
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "item_groups": []}

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    rows = frappe.db.sql(
        f"""
        SELECT i.item_group AS item_group, SUM(pii.stock_qty) AS sales_qty, SUM(pii.base_net_amount) AS sales_amount
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        INNER JOIN `tabItem` i ON i.name = pii.item_code
        WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY i.item_group
        ORDER BY sales_amount DESC
        """,
        {"company": company, "date_from": date_from, "date_to": date_to, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )
    # Calculate contribution against every qualifying item group, not only the
    # returned page. Computing the denominator after SQL LIMIT would make each
    # returned row's share add up to 100% even when lower-ranked groups exist.
    total_sales = sum(flt(r.sales_amount) for r in rows)
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "item_groups": [
            {
                "item_group": r.item_group,
                "sales_qty": flt(r.sales_qty),
                "sales_amount": flt(r.sales_amount),
                "share_pct": round(flt(r.sales_amount) / total_sales * 100, 2) if total_sales else 0,
            }
            for r in rows[:limit]
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Profitability
# ═══════════════════════════════════════════════════════════════════════════

def get_selling_below_cost(branch: str | None = None, limit: int = 30) -> dict:
    """Items whose current selling rate (an active selling Item Price row) is below
    the latest known purchase cost incl. taxes (Item.custom_latest_price_incl_taxes,
    kept live by item_pricing on every PI/PR submit - see CLAUDE.md). A real,
    actionable margin-erosion signal not covered by any other tool. Not branch-
    scoped: Item Price rows are already branch-specific via their own price_list
    name (returned per row), and this must never call
    shelf_pricing.utils.get_or_create_branch_price_list (a write) to resolve one -
    same read-only precedent as storefront_api.utils.resolve_branch_price_list."""
    limit = max(1, min(cint(limit or 30), 100))
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "items": []}

    price_list_filter: list[str] | None = None
    if branch_filter is not None:
        price_list_filter = frappe.get_all(
            "Branch",
            filters={"name": ["in", branch_filter], "company": company},
            pluck="default_selling_price_list",
        )
        price_list_filter = [p for p in price_list_filter if p]
        if not price_list_filter:
            return {"company": company, "currency": currency, "items": []}

    price_list_clause = "AND ip.price_list IN %(price_lists)s" if price_list_filter is not None else ""

    rows = frappe.db.sql(
        f"""
        SELECT ip.item_code, i.item_name, ip.price_list, ip.price_list_rate AS selling_rate,
               i.custom_latest_price_incl_taxes AS latest_cost
        FROM `tabItem Price` ip
        INNER JOIN `tabItem` i ON i.name = ip.item_code
        WHERE ip.selling = 1 AND IFNULL(i.disabled, 0) = 0
          AND i.custom_latest_price_incl_taxes > 0
          AND ip.price_list_rate < i.custom_latest_price_incl_taxes
          {price_list_clause}
        ORDER BY (i.custom_latest_price_incl_taxes - ip.price_list_rate) DESC
        LIMIT %(limit)s
        """,
        {"limit": limit, "price_lists": tuple(price_list_filter) if price_list_filter else ()},
        as_dict=True,
    )
    items = []
    for r in rows:
        selling_rate = flt(r.selling_rate)
        latest_cost = flt(r.latest_cost)
        loss_per_unit = latest_cost - selling_rate
        items.append({
            "item_code": r.item_code,
            "item_name": r.item_name,
            "price_list": r.price_list,
            "selling_rate": selling_rate,
            "latest_cost": latest_cost,
            "loss_per_unit": round(loss_per_unit, 2),
            "loss_pct": round(loss_per_unit / latest_cost * 100, 2) if latest_cost else 0,
        })
    return {"company": company, "currency": currency, "items": items}


# ═══════════════════════════════════════════════════════════════════════════
# Purchasing
# ═══════════════════════════════════════════════════════════════════════════

def get_supplier_price_comparison(item_code: str, months: int = 6, branch: str | None = None) -> dict:
    """For a single item, the most recent purchase rate paid to each distinct
    supplier in the window - distinct from get_item_price_history, which is a
    time series for one item without a per-supplier breakdown."""
    if not frappe.db.exists("Item", item_code):
        frappe.throw(_("Item {0} does not exist").format(item_code))
    months = max(1, min(cint(months or 6), 24))
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)
    date_to = getdate(today())
    date_from = add_days(date_to, -(months * 30))

    if branch_filter is not None and not branch_filter:
        return {
            "company": company,
            "currency": currency,
            "item_code": item_code,
            "item_name": frappe.get_cached_value("Item", item_code, "item_name") or item_code,
            "date_from": str(date_from),
            "date_to": str(date_to),
            "suppliers": [],
            "cheapest_supplier": None,
            "most_expensive_supplier": None,
        }

    branch_clause = "AND parent.branch IN %(branch_names)s" if branch_filter is not None else ""

    rows = frappe.db.sql(
        f"""
        SELECT child.rate, child.custom_price_after_taxes, parent.posting_date, parent.posting_time,
               parent.creation, parent.name, child.idx, parent.supplier
        FROM `tabPurchase Receipt Item` child
        INNER JOIN `tabPurchase Receipt` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1 AND parent.company = %(company)s
          AND child.item_code = %(item_code)s AND parent.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        UNION ALL
        SELECT child.rate, child.custom_price_after_taxes, parent.posting_date, parent.posting_time,
               parent.creation, parent.name, child.idx, parent.supplier
        FROM `tabPurchase Invoice Item` child
        INNER JOIN `tabPurchase Invoice` parent ON parent.name = child.parent
        WHERE parent.docstatus = 1 AND IFNULL(parent.is_return, 0) = 0 AND parent.company = %(company)s
          AND child.item_code = %(item_code)s AND parent.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
        {
            "company": company,
            "item_code": item_code,
            "date_from": date_from,
            "date_to": date_to,
            "branch_names": tuple(branch_filter) if branch_filter else (),
        },
        as_dict=True,
    )

    rows.sort(key=lambda r: (r.posting_date or "", r.posting_time or "", r.creation or "", r.name or "", r.idx or 0), reverse=True)

    latest_by_supplier: dict[str, dict] = {}
    for row in rows:
        supplier = row.supplier
        if supplier in latest_by_supplier:
            continue
        cost_val = flt(row.custom_price_after_taxes) or flt(row.rate)
        latest_by_supplier[supplier] = {
            "supplier": supplier,
            "rate": cost_val,
            "date": str(row.posting_date),
            "doc_name": row.name,
        }

    suppliers = sorted(latest_by_supplier.values(), key=lambda r: r["rate"])
    return {
        "company": company,
        "currency": currency,
        "item_code": item_code,
        "item_name": frappe.get_cached_value("Item", item_code, "item_name") or item_code,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "suppliers": suppliers,
        "cheapest_supplier": suppliers[0]["supplier"] if suppliers else None,
        "most_expensive_supplier": suppliers[-1]["supplier"] if suppliers else None,
    }


def get_po_receipt_variance(date_from: str | None = None, date_to: str | None = None, supplier: str | None = None, branch: str | None = None, limit: int = 20) -> dict:
    """Ordered (Purchase Order Item.qty) vs received (linked Purchase Receipt Item
    rows via purchase_order_item) quantity per PO line, for POs whose transaction_date
    falls in the window. Surfaces short-supply: a supplier who reliably delivers
    less than ordered. Only submitted POs/PRs count; a PO line with no linked
    receipt yet shows received_qty=0 (still pending, not necessarily short)."""
    limit = max(1, min(cint(limit or 20), 100))
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    branch_filter = _resolve_branch_filter(company, branch)
    currency = frappe.get_cached_value("Company", company, "default_currency")

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "lines": []}

    branch_clause = "AND po.branch IN %(branch_names)s" if branch_filter is not None else ""
    supplier_clause = "AND po.supplier = %(supplier)s" if supplier else ""
    params = {
        "company": company, "date_from": date_from, "date_to": date_to,
        "branch_names": tuple(branch_filter) if branch_filter else (), "supplier": supplier,
    }

    po_rows = frappe.db.sql(
        f"""
        SELECT poi.name AS po_item_name, poi.item_code, i.item_name, poi.qty AS ordered_qty,
               po.name AS po_name, po.supplier, po.transaction_date
        FROM `tabPurchase Order Item` poi
        INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
        INNER JOIN `tabItem` i ON i.name = poi.item_code
        WHERE po.docstatus = 1
          AND po.company = %(company)s
          AND po.transaction_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause} {supplier_clause}
        """,
        params,
        as_dict=True,
    )
    if not po_rows:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "lines": []}

    po_item_names = tuple(r.po_item_name for r in po_rows)
    received_rows = frappe.db.sql(
        """
        SELECT pri.purchase_order_item AS po_item_name, SUM(pri.qty) AS received_qty
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
        WHERE pr.docstatus = 1 AND IFNULL(pr.is_return, 0) = 0
          AND pri.purchase_order_item IN %(po_item_names)s
        GROUP BY pri.purchase_order_item
        """,
        {"po_item_names": po_item_names},
        as_dict=True,
    )
    received_by_line = {r.po_item_name: flt(r.received_qty) for r in received_rows}

    lines = []
    for r in po_rows:
        ordered_qty = flt(r.ordered_qty)
        received_qty = received_by_line.get(r.po_item_name, 0.0)
        variance_qty = received_qty - ordered_qty
        lines.append({
            "po_name": r.po_name,
            "supplier": r.supplier,
            "item_code": r.item_code,
            "item_name": r.item_name,
            "transaction_date": str(r.transaction_date),
            "ordered_qty": ordered_qty,
            "received_qty": received_qty,
            "variance_qty": round(variance_qty, 2),
            "variance_pct": round(variance_qty / ordered_qty * 100, 2) if ordered_qty else 0,
        })

    lines.sort(key=lambda r: r["variance_pct"])
    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "lines": lines[:limit],
    }


def get_purchase_concentration(date_from: str | None = None, date_to: str | None = None, branch: str | None = None, top_n: int = 5) -> dict:
    """Share of total purchase spend held by the top N suppliers, in a date range -
    a concentration-risk signal. Candidate spend per supplier is Purchase Invoice OR
    Purchase Receipt amount (Invoice wins when a supplier has both, matching
    rank_vendors'/get_purchase_overview's existing two-source approach so this
    doesn't double-count or return empty on a site whose purchases are recorded
    only via Purchase Receipt)."""
    top_n = max(1, min(cint(top_n or 5), 30))
    company = _resolve_company()
    date_from, date_to = _get_date_range(date_from, date_to)
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)

    if branch_filter is not None and not branch_filter:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "total_spend": 0, "top_suppliers": [], "concentration_pct": 0}

    branch_clause = "AND branch IN %(branch_names)s" if branch_filter is not None else ""
    params = {
        "company": company,
        "date_from": date_from,
        "date_to": date_to,
        "branch_names": tuple(branch_filter) if branch_filter else (),
    }

    invoice_rows = frappe.db.sql(
        f"""
        SELECT supplier, COALESCE(SUM(base_grand_total), 0) AS amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY supplier
        """,
        params,
        as_dict=True,
    )
    receipt_rows = frappe.db.sql(
        f"""
        SELECT supplier, COALESCE(SUM(base_grand_total), 0) AS amount
        FROM `tabPurchase Receipt`
        WHERE docstatus = 1 AND IFNULL(is_return, 0) = 0 AND company = %(company)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        GROUP BY supplier
        """,
        params,
        as_dict=True,
    )
    spend_by_supplier = {r.supplier: flt(r.amount) for r in receipt_rows}
    spend_by_supplier.update({r.supplier: flt(r.amount) for r in invoice_rows})

    if not spend_by_supplier:
        return {"company": company, "currency": currency, "date_from": str(date_from), "date_to": str(date_to), "total_spend": 0, "top_suppliers": [], "concentration_pct": 0}

    total_spend = sum(spend_by_supplier.values())
    ranked = sorted(spend_by_supplier.items(), key=lambda kv: -kv[1])
    top_suppliers = [{"supplier": s, "amount": amt, "share_pct": round(amt / total_spend * 100, 2) if total_spend else 0} for s, amt in ranked[:top_n]]
    top_total = sum(s["amount"] for s in top_suppliers)

    return {
        "company": company,
        "currency": currency,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "total_spend": round(total_spend, 2),
        "top_suppliers": top_suppliers,
        "concentration_pct": round(top_total / total_spend * 100, 2) if total_spend else 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Inventory
# ═══════════════════════════════════════════════════════════════════════════

def get_stock_aging(branch: str | None = None, limit: int = 30) -> dict:
    """Current stock value per item alongside days since its last GOODS RECEIPT
    (Purchase Receipt/Purchase Invoice inbound Stock Ledger Entry) - distinct from
    get_dead_stock_detail, which measures days since last SALE. An item can be
    "fresh" by dead-stock's measure (sold recently) while still holding old,
    slow-turning receipt batches, which this surfaces instead."""
    limit = max(1, min(cint(limit or 30), 100))
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)
    warehouse_filter = _branch_warehouses(branch_filter)

    if warehouse_filter is not None and not warehouse_filter:
        return {"company": company, "currency": currency, "items": []}

    warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    sle_warehouse_clause = "AND warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    params = {"company": company, "warehouse_names": tuple(warehouse_filter) if warehouse_filter else ()}

    stock_rows = frappe.db.sql(
        f"""
        SELECT b.item_code, i.item_name, SUM(b.actual_qty) AS stock_qty, SUM(b.stock_value) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE w.company = %(company)s {warehouse_clause}
        GROUP BY b.item_code, i.item_name
        HAVING SUM(b.stock_value) > 0.0001
        """,
        params,
        as_dict=True,
    )
    if not stock_rows:
        return {"company": company, "currency": currency, "items": []}

    item_codes = tuple(r.item_code for r in stock_rows)
    last_receipt_rows = frappe.db.sql(
        f"""
        SELECT item_code, MAX(posting_date) AS last_receipt_date
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0 AND company = %(company)s
          AND voucher_type IN ('Purchase Receipt', 'Purchase Invoice') AND actual_qty > 0
          AND item_code IN %(item_codes)s
          {sle_warehouse_clause}
        GROUP BY item_code
        """,
        {**params, "item_codes": item_codes},
        as_dict=True,
    )
    last_receipt_by_item = {r.item_code: r.last_receipt_date for r in last_receipt_rows}

    today_date = getdate(today())
    results = []
    for row in stock_rows:
        last_receipt = last_receipt_by_item.get(row.item_code)
        days_since = (today_date - getdate(last_receipt)).days if last_receipt else None
        results.append({
            "item_code": row.item_code,
            "item_name": row.item_name,
            "stock_qty": flt(row.stock_qty),
            "stock_value": flt(row.stock_value),
            "last_receipt_date": str(last_receipt) if last_receipt else None,
            "days_since_last_receipt": days_since,
        })

    results.sort(key=lambda r: (r["days_since_last_receipt"] is not None, -(r["days_since_last_receipt"] or 0)))
    return {"company": company, "currency": currency, "items": results[:limit]}


def get_reorder_recommendations(branch: str | None = None, lookback_days: int = 30, lead_time_days: int = 7, limit: int = 20) -> dict:
    """Closes a known gap: no dedicated reorder/replenishment tool existed before
    this (see CLAUDE.md's offline_pos note on "what should I purchase" questions
    being reframed onto get_inventory_vs_sales with no explicit reorder point).
    For each actively-selling item: daily sales velocity over lookback_days,
    reorder_point = daily_rate * lead_time_days * 1.5 (a 50% safety buffer - a
    simple heuristic, not a statistical EOQ/service-level model), flagged when
    current stock is below it. suggested_order_qty tops stock back up to 2x the
    reorder point. Sorted by days_of_stock ascending (most urgent first)."""
    lookback_days = max(1, min(cint(lookback_days or 30), 180))
    lead_time_days = max(1, min(cint(lead_time_days or 7), 60))
    limit = max(1, min(cint(limit or 20), 50))
    company = _resolve_company()
    currency = frappe.get_cached_value("Company", company, "default_currency")
    branch_filter = _resolve_branch_filter(company, branch)
    warehouse_filter = _branch_warehouses(branch_filter)

    if warehouse_filter is not None and not warehouse_filter:
        return {"company": company, "currency": currency, "lead_time_days": lead_time_days, "items": []}

    date_to = getdate(today())
    date_from = add_days(date_to, -(lookback_days - 1))
    warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    sle_warehouse_clause = "AND warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    params = {"company": company, "date_from": date_from, "date_to": date_to, "warehouse_names": tuple(warehouse_filter) if warehouse_filter else ()}

    stock_rows = frappe.db.sql(
        f"""
        SELECT b.item_code, i.item_name, SUM(b.actual_qty) AS stock_qty
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE w.company = %(company)s {warehouse_clause}
        GROUP BY b.item_code, i.item_name
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
        HAVING SUM(-actual_qty) > 0
        """,
        params,
        as_dict=True,
    )
    stock_by_item = {r.item_code: r for r in stock_rows}

    results = []
    for r in sales_rows:
        stock = stock_by_item.get(r.item_code)
        stock_qty = flt(stock.stock_qty) if stock else 0.0
        item_name = stock.item_name if stock else (frappe.get_cached_value("Item", r.item_code, "item_name") or r.item_code)
        daily_rate = flt(r.sales_qty) / lookback_days
        if daily_rate <= 0:
            continue
        reorder_point = daily_rate * lead_time_days * 1.5
        days_of_stock = stock_qty / daily_rate
        if stock_qty >= reorder_point:
            continue
        suggested_order_qty = max(0.0, reorder_point * 2 - stock_qty)
        results.append({
            "item_code": r.item_code,
            "item_name": item_name,
            "current_stock": stock_qty,
            "daily_sales_rate": round(daily_rate, 2),
            "days_of_stock": round(days_of_stock, 1),
            "reorder_point": round(reorder_point, 1),
            "suggested_order_qty": round(suggested_order_qty, 1),
        })

    results.sort(key=lambda r: r["days_of_stock"])
    return {
        "company": company,
        "currency": currency,
        "lookback_days": lookback_days,
        "lead_time_days": lead_time_days,
        "items": results[:limit],
    }


def get_negative_stock_check(branch: str | None = None, limit: int = 50) -> dict:
    """Bin rows with negative actual_qty - a data-quality/stock-integrity signal
    (should never legitimately happen; usually a sign of a missed/late stock entry
    or a reconciliation gap)."""
    limit = max(1, min(cint(limit or 50), 200))
    company = _resolve_company()
    branch_filter = _resolve_branch_filter(company, branch)
    warehouse_filter = _branch_warehouses(branch_filter)

    if warehouse_filter is not None and not warehouse_filter:
        return {"company": company, "items": []}

    warehouse_clause = "AND b.warehouse IN %(warehouse_names)s" if warehouse_filter is not None else ""
    rows = frappe.db.sql(
        f"""
        SELECT b.item_code, i.item_name, b.warehouse, b.actual_qty
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE w.company = %(company)s AND b.actual_qty < 0 {warehouse_clause}
        ORDER BY b.actual_qty ASC
        LIMIT %(limit)s
        """,
        {"company": company, "warehouse_names": tuple(warehouse_filter) if warehouse_filter else (), "limit": limit},
        as_dict=True,
    )
    return {
        "company": company,
        "items": [{"item_code": r.item_code, "item_name": r.item_name, "warehouse": r.warehouse, "actual_qty": flt(r.actual_qty)} for r in rows],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Customers
# ═══════════════════════════════════════════════════════════════════════════

def get_customer_activity_segments(lookback_days: int = 90, branch: str | None = None) -> dict:
    """Segments customers into New/Active/Lapsing/Lost based on their first and
    last POS purchase dates (across all history, not just the window), using a
    fixed window of lookback_days: New = first purchase within the window;
    Active = purchased within the window but had purchased before it too;
    Lapsing = last purchase in the prior equal-length window but nothing since;
    Lost = last purchase older than two windows ago. A simple recency-based
    proxy for retention, not a full RFM/CLV model."""
    lookback_days = max(1, min(cint(lookback_days or 90), 365))
    company = _resolve_company()
    branch_filter = _resolve_branch_filter(company, branch)

    if branch_filter is not None and not branch_filter:
        return {"company": company, "lookback_days": lookback_days, "segments": {"new": 0, "active": 0, "lapsing": 0, "lost": 0}}

    today_date = getdate(today())
    window_start = add_days(today_date, -lookback_days)
    prior_window_start = add_days(today_date, -(lookback_days * 2))

    branch_clause = "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""
    rows = frappe.db.sql(
        f"""
        SELECT pi.customer, MIN(pi.posting_date) AS first_purchase, MAX(pi.posting_date) AS last_purchase
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0 AND pi.company = %(company)s
          {branch_clause}
        GROUP BY pi.customer
        """,
        {"company": company, "branch_names": tuple(branch_filter) if branch_filter else ()},
        as_dict=True,
    )

    segments = {"new": 0, "active": 0, "lapsing": 0, "lost": 0}
    for r in rows:
        first_purchase = getdate(r.first_purchase)
        last_purchase = getdate(r.last_purchase)
        if first_purchase >= window_start:
            segments["new"] += 1
        elif last_purchase >= window_start:
            segments["active"] += 1
        elif last_purchase >= prior_window_start:
            segments["lapsing"] += 1
        else:
            segments["lost"] += 1

    return {
        "company": company,
        "lookback_days": lookback_days,
        "as_of_date": str(today_date),
        "total_customers": len(rows),
        "segments": segments,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Operational control
# ═══════════════════════════════════════════════════════════════════════════

_OPEN_DOC_TYPES = {
    "Purchase Order": "transaction_date",
    "Purchase Receipt": "posting_date",
    "Purchase Invoice": "posting_date",
    "Sales Order": "transaction_date",
}


def get_open_documents_overview(stale_days: int = 7, branch: str | None = None) -> dict:
    """Counts of draft (docstatus=0) documents across the 4 main transaction
    doctypes, plus how many are older than stale_days (drafts that likely need
    attention rather than being genuinely in-progress), and POS shifts that have
    been open longer than stale_days."""
    stale_days = max(1, min(cint(stale_days or 7), 90))
    company = _resolve_company()
    branch_filter = _resolve_branch_filter(company, branch)
    today_date = getdate(today())
    stale_cutoff = add_days(today_date, -stale_days)

    doc_summary = []
    for doctype, date_field in _OPEN_DOC_TYPES.items():
        filters = {"docstatus": 0, "company": company}
        if branch_filter is not None:
            filters["branch"] = ["in", branch_filter]
        total = frappe.db.count(doctype, filters)
        stale_filters = {**filters, date_field: ["<", stale_cutoff]}
        stale = frappe.db.count(doctype, stale_filters)
        oldest = frappe.db.get_value(doctype, filters, date_field, order_by=f"{date_field} asc")
        doc_summary.append({
            "doctype": doctype,
            "draft_count": total,
            "stale_count": stale,
            "oldest_date": str(oldest) if oldest else None,
        })

    shift_branch_clause = "AND pp.branch IN %(branch_names)s" if branch_filter is not None else ""
    openings = frappe.db.sql(
        f"""
        SELECT poe.name, poe.pos_profile, poe.user, poe.period_start_date
        FROM `tabPOS Opening Entry` poe
        LEFT JOIN `tabPOS Profile` pp ON pp.name = poe.pos_profile
        WHERE poe.company = %(company)s AND poe.docstatus = 1 AND poe.status = 'Open'
          AND poe.period_start_date < %(stale_cutoff)s
          {shift_branch_clause}
        ORDER BY poe.period_start_date ASC
        """,
        {
            "company": company,
            "stale_cutoff": stale_cutoff,
            "branch_names": tuple(branch_filter) if branch_filter else (),
        },
        as_dict=True,
    )
    stale_shifts = [
        {"name": o.name, "pos_profile": o.pos_profile, "user": o.user, "opened_at": str(o.period_start_date), "days_open": (today_date - getdate(o.period_start_date)).days}
        for o in openings
    ]

    return {
        "company": company,
        "stale_days": stale_days,
        "documents": doc_summary,
        "stale_shifts": stale_shifts,
    }


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_sales_trend",
            "description": "Get net sales bucketed by day, week, or month over a date range - use for any question about a sales trend over time. Defaults to a trailing 30-day window if no dates are given, not just today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to 30 days ago if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to today if omitted."},
                    "granularity": {"type": "string", "enum": ["day", "week", "month"], "description": "Bucket size, default day."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hourly_sales_pattern",
            "description": "Get net sales broken down by hour-of-day and by weekday over a date range. Use for questions about peak hours, busiest days, or sales patterns by time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to 30 days ago if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to today if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_discount_overview",
            "description": "Get total discount given (header + item-level) on POS sales for a date range, with a per-branch breakdown and effective discount %. Use for questions about discount leakage or how much was discounted.",
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
            "name": "get_sales_by_item_group",
            "description": "Get revenue contribution by item group (category) for a date range. Use for questions about which category/department is selling best, or category contribution to revenue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "limit": {"type": "integer", "description": "Max item groups to return, default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_selling_below_cost",
            "description": "Get items currently priced to sell below their latest known purchase cost - a margin-erosion / pricing-mistake signal. Use for questions about items losing money, underpriced items, or negative margin on specific products.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max items to return, default 30, max 100."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope selling price lists to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_supplier_price_comparison",
            "description": "For a single item, compare the most recent purchase rate paid to every supplier who has sold it in the window. Use for questions like 'which supplier gives us the best price on item X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_code": {"type": "string", "description": "Exact Item code to compare across suppliers."},
                    "months": {"type": "integer", "description": "Lookback window in months, default 6, max 24."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope purchase history to. Omit for all branches the user can see."},
                },
                "required": ["item_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_po_receipt_variance",
            "description": "Compare ordered vs. received quantity per Purchase Order line for POs in a date range - surfaces suppliers who short-deliver. Use for questions about short supply, under-delivery, or ordered-vs-received quantity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date (PO transaction_date), YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "supplier": {"type": "string", "description": "Exact Supplier name to narrow to one vendor. Omit for all suppliers."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "limit": {"type": "integer", "description": "Max lines to return, default 20, max 100."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_purchase_concentration",
            "description": "Get the share of total purchase spend held by the top N suppliers, for a date range - a vendor concentration/dependency-risk signal. Use for questions about supplier dependency or purchase concentration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": "string", "description": "Start date, YYYY-MM-DD. Defaults to today if omitted."},
                    "date_to": {"type": "string", "description": "End date, YYYY-MM-DD. Defaults to date_from if omitted."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope purchases to. Omit for all branches the user can see."},
                    "top_n": {"type": "integer", "description": "How many top suppliers to include, default 5, max 30."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_aging",
            "description": "Get current stock value per item alongside days since its last goods receipt (not last sale - use get_dead_stock_detail for that). Use for questions about aging inventory or how old the stock on hand is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "limit": {"type": "integer", "description": "Max items to return, default 30, max 100."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reorder_recommendations",
            "description": "Get items that are below their calculated reorder point (based on recent sales velocity, current stock, and a lead time), with a suggested order quantity. This is THE tool for 'what should I purchase/order/restock' questions - always prefer this over get_inventory_vs_sales for that specific phrasing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "lookback_days": {"type": "integer", "description": "Window to measure sales velocity, default 30, max 180."},
                    "lead_time_days": {"type": "integer", "description": "Assumed supplier lead time in days, default 7, max 60."},
                    "limit": {"type": "integer", "description": "Max items to return, default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_negative_stock_check",
            "description": "Get Bin rows with negative stock quantity - a data-quality/stock-integrity issue that should never legitimately happen. Use for questions about negative stock or stock discrepancies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                    "limit": {"type": "integer", "description": "Max rows to return, default 50, max 200."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_activity_segments",
            "description": "Get customer counts segmented into New/Active/Lapsing/Lost based on purchase recency. Use for questions about customer retention, churn, or how many customers are new vs. returning vs. lost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lookback_days": {"type": "integer", "description": "Segmentation window in days, default 90, max 365."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope to. Omit for all branches the user can see."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_documents_overview",
            "description": "Get counts of draft (unsubmitted) Purchase Order/Purchase Receipt/Purchase Invoice/Sales Order documents, how many are stale (older than a threshold), and POS shifts open longer than that threshold. Use for questions about pending/unsubmitted documents or shifts open too long.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stale_days": {"type": "integer", "description": "Age threshold in days to flag as stale, default 7, max 90."},
                    "branch": {"type": "string", "description": "Exact Branch name to scope draft documents and open shifts to. Omit for all branches the user can see."},
                },
            },
        },
    },
]

TOOL_DISPATCH = {
    "get_sales_trend": get_sales_trend,
    "get_hourly_sales_pattern": get_hourly_sales_pattern,
    "get_discount_overview": get_discount_overview,
    "get_sales_by_item_group": get_sales_by_item_group,
    "get_selling_below_cost": get_selling_below_cost,
    "get_supplier_price_comparison": get_supplier_price_comparison,
    "get_po_receipt_variance": get_po_receipt_variance,
    "get_purchase_concentration": get_purchase_concentration,
    "get_stock_aging": get_stock_aging,
    "get_reorder_recommendations": get_reorder_recommendations,
    "get_negative_stock_check": get_negative_stock_check,
    "get_customer_activity_segments": get_customer_activity_segments,
    "get_open_documents_overview": get_open_documents_overview,
}
