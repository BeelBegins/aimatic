import frappe
from frappe.utils import flt

# Cost price (excl/incl tax) exposes actual purchase cost, unlike the plain
# selling-price read in price_check/shelf_pricing - gate it the same way
# shelf_pricing.api/foodpanda_price_import.api gate any cost-sensitive
# read/write, not the broader Item-read check price_check uses. This is the
# entry-point security boundary for the whole module (matching vendor_
# performance/sales_dashboard's own "gate at the API, query freely
# underneath" convention) - every helper below reads with ignore_permissions
# since Buying Price Control has no standing DocPerm on Item Price/Warehouse/
# Bin/Item Barcode of its own, only the Branch/Item read+report grants added
# for this report (see patches.repair_item_custom_docperms and hooks.py's
# Custom DocPerm fixture block).
ALLOWED_EXPORT_ROLES = {"Buying Price Control", "System Manager"}

_CHUNK_SIZE = 2000


def require_export_permission():
    if not ALLOWED_EXPORT_ROLES.intersection(frappe.get_roles()):
        frappe.throw(
            frappe._("You need the Buying Price Control role to view the branch price sheet."),
            frappe.PermissionError,
        )


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def _resolve_branch_price_lists(branch):
    """Read-only - deliberately does not create either list (unlike the
    Purchase Receipt shelf-pricing flow), matching get_current_branch_sale_price/
    get_current_foodpanda_price's own "a plain read must not create" rule."""
    selling_price_list = frappe.db.get_value(
        "Branch", branch, "default_selling_price_list"
    ) or frappe.db.get_single_value("Selling Settings", "selling_price_list")
    foodpanda_price_list = frappe.db.get_value("Branch", branch, "default_foodpanda_price_list")
    return selling_price_list, foodpanda_price_list


def _fetch_price_map_by_uom(price_list, item_codes):
    """{item_code: {uom: price_list_rate}} - an item can carry more than one
    selling Item Price row on the same price list at different UOMs (e.g. a
    Pcs rate and a separate, independently-entered Box rate). Collapsing
    these into one number per item (the previous behaviour) picked whichever
    row the query happened to return last, which is how a real Box rate
    (3050) once got treated as the Pcs selling price and then multiplied by
    the Box conversion factor again (3050 x 5) - see get_branch_price_sheet_rows.
    """
    if not price_list:
        return {}
    prices = {}
    for chunk in _chunks(item_codes, _CHUNK_SIZE):
        for row in frappe.get_all(
            "Item Price",
            filters={"price_list": price_list, "selling": 1, "item_code": ("in", chunk)},
            fields=["item_code", "uom", "price_list_rate"],
            ignore_permissions=True,
        ):
            prices.setdefault(row.item_code, {})[row.uom] = flt(row.price_list_rate)
    return prices


def _fetch_barcode_map(item_codes):
    """{item_code: [barcode, ...]} - kept as a list so the report can render
    one column per barcode (barcode1, barcode2, ...) instead of a single
    comma-joined string."""
    barcodes = {}
    for chunk in _chunks(item_codes, _CHUNK_SIZE):
        for row in frappe.get_all(
            "Item Barcode",
            filters={"parent": ("in", chunk)},
            fields=["parent", "barcode", "idx"],
            order_by="idx asc",
            ignore_permissions=True,
        ):
            if row.barcode:
                barcodes.setdefault(row.parent, []).append(row.barcode)
    return barcodes


def _fetch_box_conversion_map(item_codes):
    """conversion_factor of each item's own 'Box' UOM Conversion Detail row,
    if one exists - most items have none (see the shelf-pricing/box-price
    clarification: only a real per-item Box UOM entry counts, no synthetic
    default)."""
    factors = {}
    for chunk in _chunks(item_codes, _CHUNK_SIZE):
        for row in frappe.get_all(
            "UOM Conversion Detail",
            filters={"parent": ("in", chunk), "parenttype": "Item", "uom": "Box"},
            fields=["parent", "conversion_factor"],
            ignore_permissions=True,
        ):
            factors[row.parent] = flt(row.conversion_factor)
    return factors


def _fetch_stock_map(branch, item_codes):
    warehouses = frappe.get_all(
        "Warehouse",
        filters={"custom_branch": branch, "disabled": 0},
        pluck="name",
        ignore_permissions=True,
    )
    if not warehouses:
        return {}
    stock = {}
    for chunk in _chunks(item_codes, _CHUNK_SIZE):
        rows = frappe.db.sql(
            """
            SELECT item_code, SUM(actual_qty) AS qty
            FROM `tabBin`
            WHERE warehouse IN %(warehouses)s AND item_code IN %(items)s
            GROUP BY item_code
            """,
            {"warehouses": warehouses, "items": chunk},
            as_dict=True,
        )
        for row in rows:
            stock[row.item_code] = flt(row.qty)
    return stock


_SOURCE_DOCTYPES = ("Purchase Receipt", "Purchase Invoice")


def _fetch_purchase_cost_rows(item_codes):
    """Cost candidates from the custom_price_without_taxes/custom_price_after_taxes
    fields on submitted, non-return Purchase Receipt/Purchase Invoice rows.
    These custom fields are only populated for documents entered through this
    app's own purchase forms, so coverage is partial - most items never get a
    row from this source and must fall back to _fetch_stock_entry_cost_rows.
    """
    rows_out = []
    for chunk in _chunks(item_codes, _CHUNK_SIZE):
        for source_doctype in _SOURCE_DOCTYPES:
            child_table = f"`tab{source_doctype} Item`"
            parent_table = f"`tab{source_doctype}`"
            rows = frappe.db.sql(
                f"""
                SELECT
                    child.item_code AS item_code,
                    child.custom_price_without_taxes AS excl_tax,
                    child.custom_price_after_taxes AS incl_tax,
                    parent.posting_date AS posting_date,
                    parent.posting_time AS posting_time,
                    child.creation AS creation
                FROM {child_table} child
                INNER JOIN {parent_table} parent ON parent.name = child.parent
                WHERE parent.docstatus = 1
                  AND parent.is_return = 0
                  AND child.item_code IN %(items)s
                  AND (child.custom_price_without_taxes > 0 OR child.custom_price_after_taxes > 0)
                """,
                {"items": chunk},
                as_dict=True,
            )
            rows_out.extend(rows)
    return rows_out


def _fetch_stock_entry_cost_rows(item_codes):
    """Cost candidates from plain Stock Entry rows (Material Receipt/Material
    Issue/Transfer, whichever actually carries a rate) - covers items that
    were only ever priced via a Stock Entry rather than through a Purchase
    Receipt/Invoice with this app's custom tax fields filled in, which is why
    most items were showing a blank cost before this. `basic_rate` is the
    excl-tax analogue (the entered/valuation rate before any landed cost);
    `valuation_rate` (which folds in landed-cost allocations, the closest
    Stock Entry has to an "incl. taxes/charges" figure) is used as the
    incl-tax analogue - Stock Entry has no GST-style tax breakdown of its own.
    """
    rows_out = []
    for chunk in _chunks(item_codes, _CHUNK_SIZE):
        rows = frappe.db.sql(
            """
            SELECT
                child.item_code AS item_code,
                child.basic_rate AS excl_tax,
                child.valuation_rate AS incl_tax,
                parent.posting_date AS posting_date,
                parent.posting_time AS posting_time,
                child.creation AS creation
            FROM `tabStock Entry Detail` child
            INNER JOIN `tabStock Entry` parent ON parent.name = child.parent
            WHERE parent.docstatus = 1
              AND child.item_code IN %(items)s
              AND (child.basic_rate > 0 OR child.valuation_rate > 0)
            """,
            {"items": chunk},
            as_dict=True,
        )
        rows_out.extend(rows)
    return rows_out


def _fetch_cost_price_map(item_codes):
    """Latest priced row per item across Purchase Receipt/Purchase Invoice
    (custom tax-inclusive fields) and Stock Entry (basic_rate/valuation_rate)
    combined - site-wide, not branch-filtered, matching item_pricing's own
    Item.custom_latest_price_incl_taxes convention (a single global "current
    cost", not a per-branch one). Excl-tax and incl-tax values are read off
    the same winning row so the pair is always internally consistent; the two
    sources are compared on equal footing by posting_date/posting_time/creation,
    so whichever document was actually posted most recently wins regardless
    of which source it came from.
    """
    best: dict[str, dict] = {}

    all_rows = _fetch_purchase_cost_rows(item_codes) + _fetch_stock_entry_cost_rows(item_codes)
    for row in all_rows:
        current = best.get(row.item_code)
        key = (row.posting_date, row.posting_time, row.creation)
        if current is None or key > current["_key"]:
            best[row.item_code] = {
                "excl_tax": flt(row.excl_tax),
                "incl_tax": flt(row.incl_tax),
                "_key": key,
            }

    return {code: {"excl_tax": v["excl_tax"], "incl_tax": v["incl_tax"]} for code, v in best.items()}


def get_branch_price_sheet_rows(branch):
    """Core computation backing the 'Branch Price Sheet' Script Report
    (aimatic/aimatic/report/branch_price_sheet/): every active sales Item's
    branch-scoped pricing/stock snapshot - current selling price and
    Foodpanda price (both from this branch's own Price Lists), current stock
    in hand (this branch's own warehouses), barcodes, UOM, latest purchase
    cost price excl/incl taxes (site-wide, see _fetch_cost_price_map), and
    Box price (current selling price x the item's own Box UOM conversion
    factor, where one exists). Returns a list of dicts keyed by column
    fieldname, in Item Code order.
    """
    items = frappe.get_all(
        "Item",
        filters={"disabled": 0, "is_sales_item": 1},
        fields=["item_code", "item_name", "stock_uom"],
        order_by="item_code asc",
        ignore_permissions=True,
    )
    item_codes = [i.item_code for i in items]

    selling_price_list, foodpanda_price_list = _resolve_branch_price_lists(branch)
    selling_prices_by_uom = _fetch_price_map_by_uom(selling_price_list, item_codes)
    foodpanda_prices_by_uom = _fetch_price_map_by_uom(foodpanda_price_list, item_codes)
    barcodes = _fetch_barcode_map(item_codes)
    box_factors = _fetch_box_conversion_map(item_codes)
    stock = _fetch_stock_map(branch, item_codes)
    cost_prices = _fetch_cost_price_map(item_codes)

    rows = []
    for item in items:
        code = item.item_code
        cost = cost_prices.get(code, {})
        box_factor = box_factors.get(code)

        item_prices_by_uom = selling_prices_by_uom.get(code, {})
        # An item can have its own selling rate per UOM (a Pcs row and a
        # separately-entered Box row). Prefer the row matching the item's
        # stock UOM for "current selling price"; only fall back to whatever
        # single row exists when there's no exact stock-UOM match.
        if item.stock_uom in item_prices_by_uom:
            selling_price = item_prices_by_uom[item.stock_uom]
        elif len(item_prices_by_uom) == 1:
            selling_price = next(iter(item_prices_by_uom.values()))
        else:
            selling_price = None

        foodpanda_prices_for_item = foodpanda_prices_by_uom.get(code, {})
        if item.stock_uom in foodpanda_prices_for_item:
            foodpanda_price = foodpanda_prices_for_item[item.stock_uom]
        elif len(foodpanda_prices_for_item) == 1:
            foodpanda_price = next(iter(foodpanda_prices_for_item.values()))
        else:
            foodpanda_price = None

        # If the item already carries its own explicit "Box" Item Price row,
        # that IS the box price - use it directly. Multiplying it by the Box
        # conversion factor again double-counts it (a real bug: an item
        # priced at 3050/Box was being shown as 3050 x 5 here). Only derive
        # Box price by multiplication when no explicit Box row exists.
        if "Box" in item_prices_by_uom:
            box_price = item_prices_by_uom["Box"]
        elif box_factor and selling_price:
            box_price = selling_price * box_factor
        else:
            box_price = None

        row = {
            "item_code": code,
            "item_name": item.item_name,
            "uom": item.stock_uom,
            "selling_price": selling_price,
            "foodpanda_price": foodpanda_price,
            "stock_in_hand": stock.get(code, 0),
            "cost_price_excl_tax": cost.get("excl_tax"),
            "cost_price_incl_tax": cost.get("incl_tax"),
            "box_price": box_price,
            "_barcodes": barcodes.get(code, []),
        }
        rows.append(row)

    return rows
