import frappe
from frappe.utils import flt, nowdate


def get_item_barcode(item_code, uom=None):
    """Return (barcode, barcode_uom) for an item.

    Prefers a barcode row matching the given UOM, falls back to the first
    barcode row on the item, and returns (None, None) if the item has no
    barcodes at all.
    """
    barcodes = frappe.get_all(
        "Item Barcode",
        filters={"parent": item_code},
        fields=["barcode", "uom"],
        order_by="idx asc",
    )
    if not barcodes:
        return None, None

    if uom:
        for row in barcodes:
            if row.uom == uom:
                return row.barcode, row.uom

    first = barcodes[0]
    return first.barcode, first.uom


def get_batch_dates(batch_no):
    """Return (manufacturing_date, expiry_date) for a Batch, if it exists."""
    if not batch_no:
        return None, None

    batch = frappe.db.get_value(
        "Batch", batch_no, ["manufacturing_date", "expiry_date"], as_dict=True
    )
    if not batch:
        return None, None

    return batch.manufacturing_date, batch.expiry_date


def resolve_final_date(manual_date, system_date):
    return manual_date or system_date


def get_item_price(item_code, price_list=None, company=None):
    """Best-effort selling price for an item: Item Price on the given price
    list, else the item's standard_rate, else 0."""
    price = 0

    if price_list:
        price_list_rate = frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": price_list, "selling": 1},
            "price_list_rate",
        )
        if price_list_rate:
            price = flt(price_list_rate)

    if not price:
        standard_rate = frappe.db.get_value("Item", item_code, "standard_rate")
        price = flt(standard_rate)

    return price


def get_item_mrp(item_code):
    if not frappe.get_meta("Item").has_field("custom_mrp"):
        return 0
    return flt(frappe.db.get_value("Item", item_code, "custom_mrp"))


def build_item_row(
    item_code,
    label_type,
    uom=None,
    batch_no=None,
    price_list=None,
    company=None,
    purchase_receipt_item=None,
    purchase_qty=None,
    barcode_label_qty=None,
):
    """Build a dict of values for one AIM Label Print Job Item row, resolving
    barcode, price, and system MFG/Expiry dates for the given item."""

    item = frappe.db.get_value(
        "Item",
        item_code,
        ["item_name", "item_group", "stock_uom"],
        as_dict=True,
    )
    if not item:
        frappe.throw(frappe._("Item {0} does not exist").format(item_code))

    row_uom = uom or item.stock_uom
    barcode, barcode_uom = get_item_barcode(item_code, row_uom)
    system_mfg_date, system_expiry_date = get_batch_dates(batch_no)

    row = {
        "item_code": item_code,
        "item_name": item.item_name,
        "item_group": item.item_group,
        "barcode": barcode,
        "barcode_uom": barcode_uom,
        "uom": row_uom,
        "batch_no": batch_no,
        "system_mfg_date": system_mfg_date,
        "system_expiry_date": system_expiry_date,
        "final_mfg_date": system_mfg_date,
        "final_expiry_date": system_expiry_date,
        "price": get_item_price(item_code, price_list, company),
        "mrp": get_item_mrp(item_code),
        "purchase_receipt_item": purchase_receipt_item,
        "purchase_qty": purchase_qty,
        "barcode_label_qty": barcode_label_qty if barcode_label_qty is not None else 1,
        "shelf_label_qty": 1,
        "print_label": 1,
        "remarks": "" if barcode else frappe._("No barcode found"),
    }

    if label_type == "Shelf Label":
        row["barcode_label_qty"] = 1

    return row


def compute_job_totals(job):
    total_item_rows = 0
    total_barcode_labels = 0
    total_shelf_labels = 0
    missing_barcode_count = 0

    for row in job.items:
        total_item_rows += 1

        if not row.barcode:
            missing_barcode_count += 1

        if not row.print_label:
            continue

        if job.label_type == "Shelf Label":
            total_shelf_labels += 1
        else:
            total_barcode_labels += flt(row.barcode_label_qty)

    return {
        "total_item_rows": total_item_rows,
        "total_barcode_labels": int(total_barcode_labels),
        "total_shelf_labels": int(total_shelf_labels),
        "missing_barcode_count": missing_barcode_count,
    }


def format_price(value):
    """Comma-grouped price for labels: whole numbers with no decimals
    ("7,495"), fractional amounts with two ("259.50"). Registered as a
    jinja global so print format templates don't need locale-aware
    formatting logic inline."""
    if not value:
        return ""

    value = flt(value)
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}"


def get_template_context(template_name):
    """Fetch an AIM Label Template as a plain dict, for use inside print
    format Jinja templates (registered as a jinja global method)."""
    return frappe.get_doc("AIM Label Template", template_name).as_dict()


def expand_print_rows(job):
    """Expand job.items into a flat list of individual label instances,
    repeating barcode label rows according to barcode_label_qty and shelf
    label rows exactly once each. Only rows marked print_label are included.
    """
    expanded = []
    for row in job.items:
        if not row.print_label:
            continue

        qty = 1 if job.label_type == "Shelf Label" else int(flt(row.barcode_label_qty) or 0)
        for _i in range(max(qty, 0)):
            expanded.append(row)

    return expanded
