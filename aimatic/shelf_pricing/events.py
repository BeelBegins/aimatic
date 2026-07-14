import frappe
from frappe import _
from frappe.utils import flt


def validate_shelf_price_before_submit(doc, method=None):
    """Shelf Price must never undercut cost. Only enforced for rows where a
    shelf price was actually entered - the field isn't mandatory, and many
    Purchase Receipt rows (e.g. non-retail restock) never carry one.
    """
    for row in doc.items:
        shelf_price = flt(row.get("custom_shelf_price"))
        if shelf_price <= 0:
            continue

        cost = flt(row.get("custom_price_after_taxes"))
        if shelf_price < cost:
            frappe.throw(
                _(
                    "Row #{0}: Shelf Price ({1}) cannot be less than Cost After Taxes ({2}) for item {3}"
                ).format(row.idx, shelf_price, cost, row.item_code)
            )


def restore_prices_on_cancel(doc, method=None):
    """Undo shelf_pricing's own Item Price / Item.custom_mrp writes for this
    receipt, but only where nothing more recent has since overwritten them -
    mirrors item_pricing's "does the current state still match what I last
    wrote" gate so cancelling an old Purchase Receipt can never clobber a
    newer price update from a later one.
    """
    logs = frappe.get_all(
        "Item Price Update Log",
        filters={"purchase_receipt": doc.name},
        fields=["name", "item_code", "price_list", "field_updated", "old_value", "new_value"],
    )
    if not logs:
        return

    for log in logs:
        if log.price_list:
            _restore_item_price_field(log)
        else:
            _restore_item_mrp(log)


def _restore_item_price_field(log):
    fieldname = "price_list_rate" if log.field_updated == "Rate" else "custom_mrp"
    item_price_name = frappe.db.get_value(
        "Item Price", {"item_code": log.item_code, "price_list": log.price_list, "selling": 1}, "name"
    )
    if not item_price_name:
        return

    current_value = flt(frappe.db.get_value("Item Price", item_price_name, fieldname))
    if current_value != flt(log.new_value):
        # Superseded by a later update - leave it alone.
        return

    frappe.db.set_value("Item Price", item_price_name, fieldname, log.old_value)


def _restore_item_mrp(log):
    current_value = flt(frappe.db.get_value("Item", log.item_code, "custom_mrp"))
    if current_value != flt(log.new_value):
        return

    frappe.db.set_value("Item", log.item_code, "custom_mrp", log.old_value)
