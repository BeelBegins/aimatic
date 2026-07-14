import frappe
from frappe.utils import flt, now_datetime

# Single global Foodpanda Item Price list. Not a per-branch concept, so it's
# a hardcoded constant rather than a new settings doctype - matches the
# SELLING_PRICE_LIST-constant style already used in ipos_data_migration
# scripts for a similarly single, fixed price list.
FOODPANDA_PRICE_LIST = "Foodpanda"


def _default_currency():
    return frappe.db.get_single_value("Global Defaults", "default_currency")


def get_or_create_branch_price_list(branch):
    """Return the Branch's own Selling Price List, creating and linking one
    the first time it's needed - triggered lazily by an actual Purchase
    Receipt's branch price update, never as a standalone migration step.

    On first creation, every active Item Price row from the global default
    Selling Settings.selling_price_list is copied in once so the branch
    starts from the existing price baseline, then every POS Profile for this
    branch is repointed onto the new list.

    Known gap: a POS Profile added for this branch *after* the branch price
    list already exists won't get auto-repointed here - only the moment of
    first creation does that. Not worth a separate hook for until it's
    actually hit in practice.
    """
    existing = frappe.db.get_value("Branch", branch, "default_selling_price_list")
    if existing:
        return existing

    price_list_name = f"Selling - {branch}"
    if not frappe.db.exists("Price List", price_list_name):
        frappe.get_doc(
            {
                "doctype": "Price List",
                "price_list_name": price_list_name,
                "selling": 1,
                "currency": _default_currency(),
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)

        default_price_list = (
            frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
        )
        for row in frappe.get_all(
            "Item Price",
            filters={"price_list": default_price_list, "selling": 1},
            fields=["item_code", "price_list_rate", "currency", "uom"],
        ):
            frappe.get_doc(
                {
                    "doctype": "Item Price",
                    "item_code": row.item_code,
                    "price_list": price_list_name,
                    "selling": 1,
                    "price_list_rate": row.price_list_rate,
                    "currency": row.currency or _default_currency(),
                    "uom": row.uom,
                }
            ).insert(ignore_permissions=True)

    frappe.db.set_value("Branch", branch, "default_selling_price_list", price_list_name)

    for pos_profile in frappe.get_all("POS Profile", filters={"branch": branch}, pluck="name"):
        frappe.db.set_value("POS Profile", pos_profile, "selling_price_list", price_list_name)

    return price_list_name


def get_or_create_foodpanda_price_list():
    if not frappe.db.exists("Price List", FOODPANDA_PRICE_LIST):
        frappe.get_doc(
            {
                "doctype": "Price List",
                "price_list_name": FOODPANDA_PRICE_LIST,
                "selling": 1,
                "currency": _default_currency(),
                "enabled": 1,
            }
        ).insert(ignore_permissions=True)
    return FOODPANDA_PRICE_LIST


def log_price_update(purchase_receipt, item_code, price_list, branch, field_updated, old_value, new_value):
    """One audit row per changed field. restore_prices_on_cancel relies on
    these to decide whether a cancel is still safe to roll back."""
    frappe.get_doc(
        {
            "doctype": "Item Price Update Log",
            "purchase_receipt": purchase_receipt,
            "item_code": item_code,
            "price_list": price_list,
            "branch": branch,
            "field_updated": field_updated,
            "old_value": flt(old_value),
            "new_value": flt(new_value),
            "updated_by": frappe.session.user,
            "update_datetime": now_datetime(),
        }
    ).insert(ignore_permissions=True)


def upsert_item_price(item_code, price_list, purchase_receipt, branch=None, rate=None, mrp=None):
    """Create or update the Item Price row for (item_code, price_list),
    logging every field that actually changes before overwriting it."""
    existing_name = frappe.db.get_value(
        "Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}, "name"
    )

    if existing_name:
        current = frappe.db.get_value(
            "Item Price", existing_name, ["price_list_rate", "custom_mrp"], as_dict=True
        )
        updates = {}
        if rate is not None and flt(current.price_list_rate) != flt(rate):
            log_price_update(
                purchase_receipt, item_code, price_list, branch, "Rate", current.price_list_rate, rate
            )
            updates["price_list_rate"] = rate
        if mrp is not None and flt(current.custom_mrp) != flt(mrp):
            log_price_update(purchase_receipt, item_code, price_list, branch, "MRP", current.custom_mrp, mrp)
            updates["custom_mrp"] = mrp
        if updates:
            frappe.db.set_value("Item Price", existing_name, updates)
        return existing_name

    doc = frappe.get_doc(
        {
            "doctype": "Item Price",
            "item_code": item_code,
            "price_list": price_list,
            "selling": 1,
            "currency": _default_currency(),
            "price_list_rate": rate or 0,
            "custom_mrp": mrp or 0,
        }
    )
    doc.insert(ignore_permissions=True)
    if rate:
        log_price_update(purchase_receipt, item_code, price_list, branch, "Rate", 0, rate)
    if mrp:
        log_price_update(purchase_receipt, item_code, price_list, branch, "MRP", 0, mrp)
    return doc.name
