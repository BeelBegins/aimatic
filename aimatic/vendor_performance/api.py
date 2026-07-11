
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

# Replaying stock ledger FIFO layers for exact vendor-origin stock is O(all-time
# ledger entries) for the linked item set. Past this many rows it's too slow to
# compute inline on a drill-through click, so the caller is told to narrow down
# instead of the request hanging.
_MAX_ORIGIN_STOCK_SLE_ROWS = 150_000


def _resolve_context(supplier: str, company: str | None):
    if frappe.session.user == 'Guest':
        frappe.throw(_('Login required.'))

    if not supplier:
        frappe.throw(_('Supplier is required.'))

    company = company or frappe.defaults.get_user_default('Company')
    if not company:
        frappe.throw(_('Company is required.'))

    if not frappe.has_permission('Supplier', ptype='read', doc=supplier):
        frappe.throw(_('Not permitted to view this supplier.'), frappe.PermissionError)

    supplier_meta = frappe.db.get_value(
        'Supplier',
        supplier,
        ['supplier_name', 'supplier_group'],
        as_dict=True,
    )
    if not supplier_meta:
        frappe.throw(_('Supplier {0} was not found.').format(frappe.bold(supplier)))

    currency = frappe.db.get_value('Company', company, 'default_currency')
    return supplier, company, supplier_meta, currency


def _get_date_window(lookback_days: int):
    lookback_days = max(1, min(cint(lookback_days or 30), 365))
    date_to = getdate(today())
    date_from = getdate(add_days(date_to, -(lookback_days - 1)))
    return lookback_days, date_from, date_to


@frappe.whitelist()
def get_vendor_performance_summary(supplier: str, company: str | None = None, lookback_days: int = 30):
    """Cheap, aggregate-only KPIs for the top of the page. No per-item lists and
    no stock-ledger replay - those are fetched separately via the drill-through
    endpoints below only when the user asks for them."""
    supplier, company, supplier_meta, currency = _resolve_context(supplier, company)
    lookback_days, date_from, date_to = _get_date_window(lookback_days)

    item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
    stock_summary, _rows = _get_stock_position(item_codes=item_codes, company=company, item_limit=1)
    sales_summary = _get_sales_summary(item_codes=item_codes, company=company, date_from=date_from, date_to=date_to)
    cogs_summary = _get_cogs_summary(item_codes=item_codes, company=company, date_from=date_from, date_to=date_to)
    purchase_summary = _get_purchase_summary(supplier=supplier, company=company, date_from=date_from, date_to=date_to)
    outstanding_summary = _get_outstanding_summary(supplier=supplier, company=company)
    last_payment = _get_last_payment(supplier=supplier, company=company)

    sales_amount = flt(sales_summary.get('sales_amount'))
    cogs_amount = flt(cogs_summary.get('cogs_amount'))
    gross_margin_amount = sales_amount - cogs_amount
    gross_margin_pct = (gross_margin_amount / sales_amount * 100) if sales_amount else 0

    return {
        'supplier': supplier,
        'supplier_name': supplier_meta.supplier_name or supplier,
        'supplier_group': supplier_meta.supplier_group,
        'company': company,
        'currency': currency,
        'date_from': str(date_from),
        'date_to': str(date_to),
        'lookback_days': lookback_days,
        'stock_definition_note': _('Stock position shows current stock of supplier-linked SKUs valued at cost, not exact remaining units by supplier origin.'),
        'origin_stock_definition_note': _('Exact vendor-origin stock (at cost) is computed by replaying stock ledger FIFO layers and attributing incoming Purchase Receipt and Purchase Invoice stock to the source supplier. Loaded on demand since it can be heavy for high-volume vendors.'),
        'item_sources_note': _('Supplier-linked SKUs are resolved from Item Supplier, Item Default, and actual submitted purchase history.'),
        'cogs_definition_note': _('Cost of goods sold is the cost-basis value (from stock ledger valuation) of supplier-linked SKUs consumed by submitted sales in the selected window - not the retail sales revenue.'),
        'summary': {
            'linked_item_count': len(item_codes),
            'stock_item_count': cint(stock_summary.get('item_count')),
            'stock_qty': flt(stock_summary.get('stock_qty')),
            'stock_value': flt(stock_summary.get('stock_value')),
            'sales_qty': flt(sales_summary.get('sales_qty')),
            'sales_amount': sales_amount,
            'sales_doc_count': cint(sales_summary.get('doc_count')),
            'cogs_qty': flt(cogs_summary.get('cogs_qty')),
            'cogs_amount': cogs_amount,
            'gross_margin_amount': gross_margin_amount,
            'gross_margin_pct': gross_margin_pct,
            'purchase_qty': flt(purchase_summary.get('purchase_qty')),
            'purchase_amount': flt(purchase_summary.get('purchase_amount')),
            'purchase_doc_count': cint(purchase_summary.get('doc_count')),
            'outstanding_amount': flt(outstanding_summary.get('outstanding_amount')),
            'outstanding_invoice_count': cint(outstanding_summary.get('invoice_count')),
        },
        'last_payment': last_payment,
    }


@frappe.whitelist()
def get_vendor_stock_detail(supplier: str, company: str | None = None, lookback_days: int = 30, item_limit: int = 15):
    """Drill-through: current stock of supplier-linked SKUs, top N by value."""
    supplier, company, _meta, currency = _resolve_context(supplier, company)
    lookback_days, date_from, date_to = _get_date_window(lookback_days)
    item_limit = max(1, min(cint(item_limit or 15), 50))

    item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
    stock_summary, stock_rows = _get_stock_position(item_codes=item_codes, company=company, item_limit=item_limit)

    if stock_rows:
        row_item_codes = [row['item_code'] for row in stock_rows]
        sales_by_item = _get_sales_by_item(
            item_codes=row_item_codes,
            company=company,
            date_from=date_from,
            date_to=date_to,
        )
        cogs_by_item = _get_cogs_by_item(
            item_codes=row_item_codes,
            company=company,
            date_from=date_from,
            date_to=date_to,
        )
        last_purchase_by_item = _get_last_purchase_by_item(
            supplier=supplier,
            company=company,
            item_codes=row_item_codes,
        )
        for row in stock_rows:
            sales_item = sales_by_item.get(row['item_code'], {})
            cogs_item = cogs_by_item.get(row['item_code'], {})
            last_purchase = last_purchase_by_item.get(row['item_code'], {})
            row['sales_qty_in_window'] = flt(sales_item.get('sales_qty'))
            row['sales_amount_in_window'] = flt(sales_item.get('sales_amount'))
            row['cogs_qty_in_window'] = flt(cogs_item.get('cogs_qty'))
            row['cogs_amount_in_window'] = flt(cogs_item.get('cogs_amount'))
            row['gross_margin_in_window'] = row['sales_amount_in_window'] - row['cogs_amount_in_window']
            row['last_purchase_date'] = last_purchase.get('posting_date')
            row['last_purchase_rate'] = flt(last_purchase.get('rate'))
            row['last_purchase_doc'] = last_purchase.get('purchase_invoice')
            row['last_purchase_doctype'] = last_purchase.get('doctype')

    return {
        'currency': currency,
        'summary': {
            'stock_item_count': cint(stock_summary.get('item_count')),
            'stock_qty': flt(stock_summary.get('stock_qty')),
            'stock_value': flt(stock_summary.get('stock_value')),
        },
        'stock_items': stock_rows,
    }


@frappe.whitelist()
def get_vendor_origin_stock_detail(supplier: str, company: str | None = None, item_limit: int = 15):
    """Drill-through: exact vendor-origin remaining stock via FIFO replay.
    Guarded by _MAX_ORIGIN_STOCK_SLE_ROWS - a supplier with a very large linked-item
    ledger history returns too_large=True instead of blocking on the full replay."""
    supplier, company, _meta, currency = _resolve_context(supplier, company)
    item_limit = max(1, min(cint(item_limit or 15), 50))

    empty_summary = {'item_count': 0, 'stock_qty': 0, 'stock_value': 0, 'unattributed_qty': 0, 'unattributed_value': 0}
    item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
    if not item_codes:
        return {'currency': currency, 'too_large': False, 'sle_row_count': 0, 'summary': empty_summary, 'origin_stock_items': []}

    sle_row_count = cint(
        frappe.db.sql(
            '''
            SELECT COUNT(*)
            FROM `tabStock Ledger Entry`
            WHERE is_cancelled = 0
              AND company = %(company)s
              AND item_code IN %(item_codes)s
            ''',
            {'company': company, 'item_codes': tuple(item_codes)},
        )[0][0]
    )

    if sle_row_count > _MAX_ORIGIN_STOCK_SLE_ROWS:
        return {
            'currency': currency,
            'too_large': True,
            'sle_row_count': sle_row_count,
            'summary': empty_summary,
            'origin_stock_items': [],
        }

    origin_stock_summary, origin_stock_rows = _get_exact_vendor_origin_stock(
        supplier=supplier,
        company=company,
        item_codes=item_codes,
        item_limit=item_limit,
    )
    return {
        'currency': currency,
        'too_large': False,
        'sle_row_count': sle_row_count,
        'summary': {
            'item_count': cint(origin_stock_summary.get('item_count')),
            'stock_qty': flt(origin_stock_summary.get('stock_qty')),
            'stock_value': flt(origin_stock_summary.get('stock_value')),
            'unattributed_qty': flt(origin_stock_summary.get('unattributed_qty')),
            'unattributed_value': flt(origin_stock_summary.get('unattributed_value')),
        },
        'origin_stock_items': origin_stock_rows,
    }


@frappe.whitelist()
def get_vendor_recent_purchases(supplier: str, company: str | None = None, limit: int = 3):
    """Drill-through: most recent submitted purchase invoices for this supplier."""
    supplier, company, _meta, _currency = _resolve_context(supplier, company)
    limit = max(1, min(cint(limit or 3), 20))
    return {'recent_purchases': _get_recent_purchases(supplier=supplier, company=company, limit=limit)}


@frappe.whitelist()
def get_vendor_recent_sales(supplier: str, company: str | None = None, lookback_days: int = 30, limit: int = 3):
    """Drill-through: most recent sales of supplier-linked SKUs within the window."""
    supplier, company, _meta, _currency = _resolve_context(supplier, company)
    _lookback_days, date_from, date_to = _get_date_window(lookback_days)
    limit = max(1, min(cint(limit or 3), 20))

    item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
    recent_sales = _get_recent_sales(item_codes=item_codes, company=company, date_from=date_from, date_to=date_to, limit=limit)

    cogs_by_voucher = _get_cogs_by_voucher(
        voucher_names=[row['name'] for row in recent_sales],
        item_codes=item_codes,
        company=company,
    )
    for row in recent_sales:
        row['cogs_amount'] = flt(cogs_by_voucher.get(row['name']))
        row['gross_margin'] = flt(row['vendor_amount']) - row['cogs_amount']

    return {'recent_sales': recent_sales}


def _get_supplier_item_codes(supplier: str, company: str) -> list[str]:
    rows = frappe.db.sql(
        '''
        SELECT DISTINCT item_code
        FROM (
            SELECT isup.parent AS item_code
            FROM `tabItem Supplier` isup
            WHERE isup.supplier = %(supplier)s

            UNION

            SELECT idef.parent AS item_code
            FROM `tabItem Default` idef
            WHERE idef.default_supplier = %(supplier)s
              AND (idef.company = %(company)s OR idef.company IS NULL OR idef.company = '')

            UNION

            SELECT pii.item_code AS item_code
            FROM `tabPurchase Invoice Item` pii
            INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.supplier = %(supplier)s
              AND IFNULL(pii.item_code, '') != ''

            UNION

            SELECT pri.item_code AS item_code
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
            WHERE pr.docstatus = 1
              AND IFNULL(pr.is_return, 0) = 0
              AND pr.company = %(company)s
              AND pr.supplier = %(supplier)s
              AND IFNULL(pri.item_code, '') != ''
        ) item_pool
        WHERE IFNULL(item_code, '') != ''
        ORDER BY item_code
        ''',
        {'supplier': supplier, 'company': company},
        as_dict=True,
    )
    return [row.item_code for row in rows]


def _get_stock_position(item_codes: list[str], company: str, item_limit: int):
    if not item_codes:
        return {'item_count': 0, 'stock_qty': 0, 'stock_value': 0}, []

    grouped_rows = frappe.db.sql(
        '''
        SELECT
            b.item_code,
            i.item_name,
            SUM(b.actual_qty) AS stock_qty,
            SUM(b.stock_value) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE b.item_code IN %(item_codes)s
          AND w.company = %(company)s
        GROUP BY b.item_code, i.item_name
        HAVING ABS(SUM(b.actual_qty)) > 0.0001 OR ABS(SUM(b.stock_value)) > 0.0001
        ORDER BY stock_value DESC, stock_qty DESC, b.item_code ASC
        ''',
        {'item_codes': tuple(item_codes), 'company': company},
        as_dict=True,
    )

    summary = {
        'item_count': len(grouped_rows),
        'stock_qty': sum(flt(row.stock_qty) for row in grouped_rows),
        'stock_value': sum(flt(row.stock_value) for row in grouped_rows),
    }

    rows = [
        {
            'item_code': row.item_code,
            'item_name': row.item_name,
            'stock_qty': flt(row.stock_qty),
            'stock_value': flt(row.stock_value),
        }
        for row in grouped_rows[:item_limit]
    ]
    return summary, rows


def _get_exact_vendor_origin_stock(supplier: str, company: str, item_codes: list[str], item_limit: int):
    if not item_codes:
        return {
            'item_count': 0,
            'stock_qty': 0,
            'stock_value': 0,
            'unattributed_qty': 0,
            'unattributed_value': 0,
        }, []

    attribution_map = _get_purchase_supplier_map(company=company, item_codes=item_codes)
    sle_rows = frappe.db.sql(
        '''
        SELECT
            name,
            item_code,
            voucher_type,
            voucher_no,
            voucher_detail_no,
            posting_date,
            posting_time,
            creation,
            actual_qty,
            stock_value_difference
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
        ORDER BY item_code ASC, posting_date ASC, posting_time ASC, creation ASC, name ASC
        ''',
        {'company': company, 'item_codes': tuple(item_codes)},
        as_dict=True,
    )

    item_names = {
        row.name: row.item_name
        for row in frappe.get_all('Item', filters={'name': ['in', item_codes]}, fields=['name', 'item_name'])
    }

    layers_by_item: dict[str, list[dict]] = {item_code: [] for item_code in item_codes}
    unattributed_qty = 0.0
    unattributed_value = 0.0

    for row in sle_rows:
        item_code = row.item_code
        qty = flt(row.actual_qty)
        value = flt(row.stock_value_difference)
        if not qty:
            continue

        if qty > 0:
            supplier_name = attribution_map.get(row.voucher_detail_no)
            layer = {
                'qty': qty,
                'value': value,
                'supplier': supplier_name,
                'voucher_type': row.voucher_type,
                'voucher_no': row.voucher_no,
                'posting_date': str(row.posting_date),
            }
            layers_by_item.setdefault(item_code, []).append(layer)
            if not supplier_name:
                unattributed_qty += qty
                unattributed_value += value
            continue

        consume_qty = abs(qty)
        item_layers = layers_by_item.setdefault(item_code, [])
        idx = 0
        while consume_qty > 0.0000001 and idx < len(item_layers):
            layer = item_layers[idx]
            layer_qty = flt(layer['qty'])
            if layer_qty <= 0.0000001:
                idx += 1
                continue
            used_qty = min(layer_qty, consume_qty)
            unit_value = flt(layer['value']) / layer_qty if layer_qty else 0
            layer['qty'] = layer_qty - used_qty
            layer['value'] = flt(layer['value']) - (unit_value * used_qty)
            if not layer.get('supplier'):
                unattributed_qty -= used_qty
                unattributed_value -= unit_value * used_qty
            consume_qty -= used_qty
            if flt(layer['qty']) <= 0.0000001:
                idx += 1

    per_item = []
    total_qty = 0.0
    total_value = 0.0
    for item_code, layers in layers_by_item.items():
        supplier_layers = [layer for layer in layers if layer.get('supplier') == supplier and flt(layer.get('qty')) > 0.0000001]
        if not supplier_layers:
            continue
        qty = sum(flt(layer['qty']) for layer in supplier_layers)
        value = sum(flt(layer['value']) for layer in supplier_layers)
        total_qty += qty
        total_value += value
        latest_layer = supplier_layers[-1]
        per_item.append(
            {
                'item_code': item_code,
                'item_name': item_names.get(item_code),
                'origin_stock_qty': qty,
                'origin_stock_value': value,
                'last_origin_doc': latest_layer.get('voucher_no'),
                'last_origin_doctype': latest_layer.get('voucher_type'),
                'last_origin_date': latest_layer.get('posting_date'),
            }
        )

    per_item.sort(key=lambda row: (-flt(row['origin_stock_value']), -flt(row['origin_stock_qty']), row['item_code']))
    summary = {
        'item_count': len(per_item),
        'stock_qty': total_qty,
        'stock_value': total_value,
        'unattributed_qty': max(flt(unattributed_qty), 0),
        'unattributed_value': max(flt(unattributed_value), 0),
    }
    return summary, per_item[:item_limit]


def _get_purchase_supplier_map(company: str, item_codes: list[str]):
    if not item_codes:
        return {}

    rows = frappe.db.sql(
        '''
        SELECT pri.name AS row_name, pr.supplier
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
        WHERE pr.docstatus = 1
          AND IFNULL(pr.is_return, 0) = 0
          AND pr.company = %(company)s
          AND pri.item_code IN %(item_codes)s

        UNION ALL

        SELECT pii.name AS row_name, pi.supplier
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pii.item_code IN %(item_codes)s
        ''',
        {'company': company, 'item_codes': tuple(item_codes)},
        as_dict=True,
    )
    return {row.row_name: row.supplier for row in rows if row.row_name and row.supplier}


def _get_sales_summary(item_codes: list[str], company: str, date_from, date_to):
    if not item_codes:
        return {'sales_qty': 0, 'sales_amount': 0, 'doc_count': 0}

    rows = frappe.db.sql(
        '''
        SELECT
            SUM(sales_qty) AS sales_qty,
            SUM(sales_amount) AS sales_amount,
            COUNT(DISTINCT docname) AS doc_count
        FROM (
            SELECT si.name AS docname, sii.stock_qty AS sales_qty, sii.base_net_amount AS sales_amount
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
            WHERE si.docstatus = 1
              AND IFNULL(si.is_return, 0) = 0
              AND si.company = %(company)s
              AND si.posting_date BETWEEN %(date_from)s AND %(date_to)s
              AND sii.item_code IN %(item_codes)s

            UNION ALL

            SELECT pi.name AS docname, pii.stock_qty AS sales_qty, pii.base_net_amount AS sales_amount
            FROM `tabPOS Invoice Item` pii
            INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
              AND pii.item_code IN %(item_codes)s
        ) sales_union
        ''',
        {'company': company, 'date_from': date_from, 'date_to': date_to, 'item_codes': tuple(item_codes)},
        as_dict=True,
    )
    row = rows[0] if rows else {}
    return {'sales_qty': flt(row.get('sales_qty')), 'sales_amount': flt(row.get('sales_amount')), 'doc_count': cint(row.get('doc_count'))}


def _get_cogs_summary(item_codes: list[str], company: str, date_from, date_to):
    """Cost of goods sold: cost-basis value of stock consumed by submitted sales in
    the window, read straight from Stock Ledger Entry valuation (stock_value_difference)
    rather than sales revenue. Consumption rows have actual_qty < 0; a Sales/POS Invoice
    return instead adds stock back in with actual_qty > 0, so it's naturally excluded
    without needing to join back to the parent doc's is_return flag."""
    if not item_codes:
        return {'cogs_qty': 0, 'cogs_amount': 0, 'doc_count': 0}

    rows = frappe.db.sql(
        '''
        SELECT
            COALESCE(SUM(-actual_qty), 0) AS cogs_qty,
            COALESCE(SUM(-stock_value_difference), 0) AS cogs_amount,
            COUNT(DISTINCT voucher_no) AS doc_count
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice')
          AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
        ''',
        {'company': company, 'item_codes': tuple(item_codes), 'date_from': date_from, 'date_to': date_to},
        as_dict=True,
    )
    row = rows[0] if rows else {}
    return {'cogs_qty': flt(row.get('cogs_qty')), 'cogs_amount': flt(row.get('cogs_amount')), 'doc_count': cint(row.get('doc_count'))}


def _get_purchase_summary(supplier: str, company: str, date_from, date_to):
    invoice_rows = frappe.db.sql(
        '''
        SELECT COUNT(*) AS doc_count, COALESCE(SUM(base_grand_total), 0) AS purchase_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1
          AND IFNULL(is_return, 0) = 0
          AND company = %(company)s
          AND supplier = %(supplier)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
        ''',
        {'supplier': supplier, 'company': company, 'date_from': date_from, 'date_to': date_to},
        as_dict=True,
    )
    qty_rows = frappe.db.sql(
        '''
        SELECT COALESCE(SUM(pii.qty), 0) AS purchase_qty
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.supplier = %(supplier)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
        ''',
        {'supplier': supplier, 'company': company, 'date_from': date_from, 'date_to': date_to},
        as_dict=True,
    )
    invoice_row = invoice_rows[0] if invoice_rows else {}
    qty_row = qty_rows[0] if qty_rows else {}
    return {
        'doc_count': cint(invoice_row.get('doc_count')),
        'purchase_qty': flt(qty_row.get('purchase_qty')),
        'purchase_amount': flt(invoice_row.get('purchase_amount')),
    }


def _get_outstanding_summary(supplier: str, company: str):
    rows = frappe.db.sql(
        '''
        SELECT COUNT(*) AS invoice_count, COALESCE(SUM(outstanding_amount), 0) AS outstanding_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1
          AND IFNULL(is_return, 0) = 0
          AND company = %(company)s
          AND supplier = %(supplier)s
          AND outstanding_amount > 0
        ''',
        {'supplier': supplier, 'company': company},
        as_dict=True,
    )
    row = rows[0] if rows else {}
    return {'invoice_count': cint(row.get('invoice_count')), 'outstanding_amount': flt(row.get('outstanding_amount'))}


def _get_last_payment(supplier: str, company: str):
    rows = frappe.db.sql(
        '''
        SELECT name, posting_date, mode_of_payment, reference_no, paid_amount, paid_from_account_currency, base_paid_amount
        FROM `tabPayment Entry`
        WHERE docstatus = 1
          AND company = %(company)s
          AND party_type = 'Supplier'
          AND party = %(supplier)s
          AND payment_type = 'Pay'
        ORDER BY posting_date DESC, modified DESC, name DESC
        LIMIT 1
        ''',
        {'supplier': supplier, 'company': company},
        as_dict=True,
    )
    row = rows[0] if rows else None
    if not row:
        return None
    return {
        'payment_entry': row.name,
        'posting_date': str(row.posting_date),
        'mode_of_payment': row.mode_of_payment,
        'reference_no': row.reference_no,
        'amount': flt(row.base_paid_amount or row.paid_amount),
        'currency': row.paid_from_account_currency,
    }


def _get_recent_purchases(supplier: str, company: str, limit: int):
    rows = frappe.db.sql(
        '''
        SELECT pi.name, pi.posting_date, pi.bill_no, pi.base_grand_total, pi.outstanding_amount, pi.status
        FROM `tabPurchase Invoice` pi
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.supplier = %(supplier)s
        ORDER BY pi.posting_date DESC, pi.modified DESC, pi.name DESC
        LIMIT %(limit)s
        ''',
        {'supplier': supplier, 'company': company, 'limit': cint(limit)},
        as_dict=True,
    )
    return [
        {
            'doctype': 'Purchase Invoice',
            'name': row.name,
            'posting_date': str(row.posting_date),
            'bill_no': row.bill_no,
            'amount': flt(row.base_grand_total),
            'outstanding_amount': flt(row.outstanding_amount),
            'status': row.status,
        }
        for row in rows
    ]


def _get_recent_sales(item_codes: list[str], company: str, date_from, date_to, limit: int):
    if not item_codes:
        return []

    rows = frappe.db.sql(
        '''
        SELECT *
        FROM (
            SELECT 'Sales Invoice' AS doctype, si.name, si.posting_date, si.customer, si.base_grand_total AS invoice_amount, SUM(sii.base_net_amount) AS vendor_amount
            FROM `tabSales Invoice` si
            INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
            WHERE si.docstatus = 1
              AND IFNULL(si.is_return, 0) = 0
              AND si.company = %(company)s
              AND si.posting_date BETWEEN %(date_from)s AND %(date_to)s
              AND sii.item_code IN %(item_codes)s
            GROUP BY si.name, si.posting_date, si.customer, si.base_grand_total

            UNION ALL

            SELECT 'POS Invoice' AS doctype, pi.name, pi.posting_date, pi.customer, pi.base_grand_total AS invoice_amount, SUM(pii.base_net_amount) AS vendor_amount
            FROM `tabPOS Invoice` pi
            INNER JOIN `tabPOS Invoice Item` pii ON pii.parent = pi.name
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
              AND pii.item_code IN %(item_codes)s
            GROUP BY pi.name, pi.posting_date, pi.customer, pi.base_grand_total
        ) sales_docs
        ORDER BY posting_date DESC, name DESC
        LIMIT %(limit)s
        ''',
        {'company': company, 'date_from': date_from, 'date_to': date_to, 'item_codes': tuple(item_codes), 'limit': cint(limit)},
        as_dict=True,
    )
    return [
        {
            'doctype': row.doctype,
            'name': row.name,
            'posting_date': str(row.posting_date),
            'party': row.customer,
            'invoice_amount': flt(row.invoice_amount),
            'vendor_amount': flt(row.vendor_amount),
        }
        for row in rows
    ]


def _get_sales_by_item(item_codes: list[str], company: str, date_from, date_to):
    if not item_codes:
        return {}

    rows = frappe.db.sql(
        '''
        SELECT item_code, SUM(sales_qty) AS sales_qty, SUM(sales_amount) AS sales_amount
        FROM (
            SELECT sii.item_code, sii.stock_qty AS sales_qty, sii.base_net_amount AS sales_amount
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
            WHERE si.docstatus = 1
              AND IFNULL(si.is_return, 0) = 0
              AND si.company = %(company)s
              AND si.posting_date BETWEEN %(date_from)s AND %(date_to)s
              AND sii.item_code IN %(item_codes)s

            UNION ALL

            SELECT pii.item_code, pii.stock_qty AS sales_qty, pii.base_net_amount AS sales_amount
            FROM `tabPOS Invoice Item` pii
            INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
              AND pii.item_code IN %(item_codes)s
        ) sales_rows
        GROUP BY item_code
        ''',
        {'company': company, 'date_from': date_from, 'date_to': date_to, 'item_codes': tuple(item_codes)},
        as_dict=True,
    )
    return {row.item_code: {'sales_qty': flt(row.sales_qty), 'sales_amount': flt(row.sales_amount)} for row in rows}


def _get_cogs_by_item(item_codes: list[str], company: str, date_from, date_to):
    if not item_codes:
        return {}

    rows = frappe.db.sql(
        '''
        SELECT
            item_code,
            SUM(-actual_qty) AS cogs_qty,
            SUM(-stock_value_difference) AS cogs_amount
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice')
          AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
        GROUP BY item_code
        ''',
        {'company': company, 'item_codes': tuple(item_codes), 'date_from': date_from, 'date_to': date_to},
        as_dict=True,
    )
    return {row.item_code: {'cogs_qty': flt(row.cogs_qty), 'cogs_amount': flt(row.cogs_amount)} for row in rows}


def _get_cogs_by_voucher(voucher_names: list[str], item_codes: list[str], company: str):
    if not voucher_names or not item_codes:
        return {}

    rows = frappe.db.sql(
        '''
        SELECT voucher_no, SUM(-stock_value_difference) AS cogs_amount
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice')
          AND voucher_no IN %(voucher_names)s
          AND actual_qty < 0
        GROUP BY voucher_no
        ''',
        {'company': company, 'item_codes': tuple(item_codes), 'voucher_names': tuple(voucher_names)},
        as_dict=True,
    )
    return {row.voucher_no: flt(row.cogs_amount) for row in rows}


def _get_last_purchase_by_item(supplier: str, company: str, item_codes: list[str]):
    if not item_codes:
        return {}

    rows = frappe.db.sql(
        '''
        SELECT *
        FROM (
            SELECT pii.item_code, 'Purchase Invoice' AS doctype, pi.name AS document_name, pi.posting_date, pii.rate, pi.modified
            FROM `tabPurchase Invoice Item` pii
            INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.supplier = %(supplier)s
              AND pii.item_code IN %(item_codes)s

            UNION ALL

            SELECT pri.item_code, 'Purchase Receipt' AS doctype, pr.name AS document_name, pr.posting_date, pri.rate, pr.modified
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
            WHERE pr.docstatus = 1
              AND IFNULL(pr.is_return, 0) = 0
              AND pr.company = %(company)s
              AND pr.supplier = %(supplier)s
              AND pri.item_code IN %(item_codes)s
        ) purchase_rows
        ORDER BY posting_date DESC, modified DESC, document_name DESC
        ''',
        {'supplier': supplier, 'company': company, 'item_codes': tuple(item_codes)},
        as_dict=True,
    )
    latest = {}
    for row in rows:
        if row.item_code not in latest:
            latest[row.item_code] = {
                'purchase_invoice': row.document_name,
                'doctype': row.doctype,
                'posting_date': str(row.posting_date),
                'rate': flt(row.rate),
            }
    return latest
