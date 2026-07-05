import frappe
from frappe import _

from aimatic.label_printing.tspl import generate_tspl_for_job
from aimatic.label_printing.utils import build_item_row


def get_default_template(label_type):
    """Best-guess default template for a label type: the first enabled
    template of that type, if any. Returns None if none are configured."""
    return frappe.db.get_value(
        "AIM Label Template",
        {"label_type": label_type, "enabled": 1},
        "name",
        order_by="creation asc",
    )


@frappe.whitelist()
def create_from_purchase_receipt(purchase_receipt, template=None):
    """Create a Draft AIM Label Print Job pre-filled from a submitted
    Purchase Receipt's items. Does not modify the Purchase Receipt."""
    frappe.has_permission("Purchase Receipt", doc=purchase_receipt, ptype="read", throw=True)
    frappe.has_permission("AIM Label Print Job", ptype="create", throw=True)

    pr = frappe.get_doc("Purchase Receipt", purchase_receipt)

    job = frappe.new_doc("AIM Label Print Job")
    job.job_title = _("Labels for {0}").format(pr.name)
    job.label_type = "Barcode Label"
    job.source_type = "Purchase Receipt"
    job.purchase_receipt = pr.name
    job.company = pr.company
    job.template = template or get_default_template("Barcode Label")

    for pr_item in pr.items:
        qty = pr_item.received_qty or pr_item.qty or 1
        row = build_item_row(
            item_code=pr_item.item_code,
            label_type="Barcode Label",
            uom=pr_item.uom,
            batch_no=pr_item.batch_no,
            price_list=None,
            company=pr.company,
            purchase_receipt_item=pr_item.name,
            purchase_qty=qty,
            barcode_label_qty=int(qty) if qty else 1,
        )
        job.append("items", row)
        if not job.warehouse and pr_item.warehouse:
            job.warehouse = pr_item.warehouse

    job.insert()
    return job.name


@frappe.whitelist()
def search_items(txt=None):
    """Search items by item code, item name, or barcode for the manual
    item-add dialog on Label Print Job."""
    txt = (txt or "").strip()
    if not txt:
        return []

    like = f"%{txt}%"

    items_by_barcode = frappe.get_all(
        "Item Barcode",
        filters={"barcode": ["like", like]},
        fields=["parent as item_code"],
        limit=20,
    )
    item_codes_from_barcode = [row.item_code for row in items_by_barcode]

    items = frappe.get_all(
        "Item",
        filters={"disabled": 0},
        or_filters=[
            ["item_code", "like", like],
            ["item_name", "like", like],
            ["item_code", "in", item_codes_from_barcode or [""]],
        ],
        fields=["item_code", "item_name", "item_group", "stock_uom"],
        limit=20,
    )

    return items


@frappe.whitelist()
def get_item_label_data(item_code, label_type="Barcode Label", uom=None, batch_no=None, price_list=None, company=None):
    """Resolve barcode/price/dates for one item, used when adding a row
    manually from the Label Print Job form."""
    frappe.has_permission("Item", doc=item_code, ptype="read", throw=True)
    return build_item_row(
        item_code=item_code,
        label_type=label_type,
        uom=uom,
        batch_no=batch_no,
        price_list=price_list,
        company=company,
    )


@frappe.whitelist()
def check_before_print(label_print_job):
    doc = frappe.get_doc("AIM Label Print Job", label_print_job)
    doc.check_permission("read")
    doc.before_print_check()
    return {"ok": True}


@frappe.whitelist()
def generate_tspl(label_print_job):
    doc = frappe.get_doc("AIM Label Print Job", label_print_job)
    doc.check_permission("read")
    doc.before_print_check()
    return generate_tspl_for_job(doc)


@frappe.whitelist()
def mark_printed(label_print_job):
    doc = frappe.get_doc("AIM Label Print Job", label_print_job)
    doc.check_permission("write")
    doc.db_set("status", "Printed")
    return {"ok": True}
