import frappe
from frappe import _
from frappe.utils import flt, getdate

from aimatic.shelf_pricing.utils import (
    get_or_create_branch_foodpanda_price_list,
    get_or_create_branch_price_list,
    log_price_update,
    upsert_item_price,
)

# Submitting a Purchase Receipt stays open to whoever can already submit one
# (Store/KPOS users). Only *applying* a price update - branch or Foodpanda -
# is gated on this role, so a "Yes" answered by someone without it just
# leaves the status Pending for a Buying Price Control user to retry later.
_ALLOWED_PRICE_UPDATE_ROLES = {"Buying Price Control", "System Manager"}


def _require_price_update_permission():
    if not _ALLOWED_PRICE_UPDATE_ROLES.intersection(frappe.get_roles()):
        frappe.throw(
            _("You need the Buying Price Control role to update selling prices."),
            frappe.PermissionError,
        )


def _get_submitted_receipt(purchase_receipt):
    doc = frappe.get_doc("Purchase Receipt", purchase_receipt)
    if doc.docstatus != 1:
        frappe.throw(_("Purchase Receipt must be submitted first."))
    return doc


@frappe.whitelist()
def apply_branch_price_update(purchase_receipt):
    doc = _get_submitted_receipt(purchase_receipt)
    if doc.custom_branch_price_update_status == "Updated":
        return {"status": "Updated"}

    _require_price_update_permission()

    if not doc.branch:
        frappe.throw(_("This receipt has no Branch set - cannot update a branch price list."))

    branch_price_list = get_or_create_branch_price_list(doc.branch)
    posting_date = getdate(doc.posting_date)

    for row in doc.items:
        shelf_price = flt(row.custom_shelf_price)
        if shelf_price <= 0:
            continue

        row_mrp = flt(row.custom_mrp)
        upsert_item_price(
            row.item_code,
            branch_price_list,
            purchase_receipt=doc.name,
            branch=doc.branch,
            rate=shelf_price,
            mrp=row_mrp or None,
        )
        _update_global_item_mrp(row.item_code, row_mrp, doc.name, posting_date)

    frappe.db.set_value("Purchase Receipt", doc.name, "custom_branch_price_update_status", "Updated")
    return {"status": "Updated"}


def _update_global_item_mrp(item_code, mrp, purchase_receipt, posting_date):
    if mrp <= 0:
        return

    current_source_date = frappe.db.get_value("Item", item_code, "custom_mrp_source_date")
    if current_source_date and getdate(current_source_date) > posting_date:
        return

    current_mrp = flt(frappe.db.get_value("Item", item_code, "custom_mrp"))
    if current_mrp != mrp:
        log_price_update(purchase_receipt, item_code, None, None, "MRP", current_mrp, mrp)

    frappe.db.set_value("Item", item_code, {"custom_mrp": mrp, "custom_mrp_source_date": posting_date})


@frappe.whitelist()
def skip_branch_price_update(purchase_receipt):
    frappe.db.set_value(
        "Purchase Receipt", purchase_receipt, "custom_branch_price_update_status", "Skipped"
    )
    return {"status": "Skipped"}


@frappe.whitelist()
def get_current_foodpanda_price(item_code, branch):
    """Read-only lookup backing the client-side FP Price prefill (see
    public/js/foodpanda_price_prefill.js) - returns whatever rate is
    currently in this branch's Foodpanda Price List for this item, or 0 if
    none yet. Deliberately does not call
    get_or_create_branch_foodpanda_price_list - a plain read must not have
    the side effect of creating that list.
    """
    if not item_code or not branch:
        return {"rate": 0}

    price_list = frappe.db.get_value("Branch", branch, "default_foodpanda_price_list")
    if not price_list:
        return {"rate": 0}

    rate = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "price_list": price_list, "selling": 1},
        "price_list_rate",
    )
    return {"rate": flt(rate)}


@frappe.whitelist()
def apply_foodpanda_price_update(purchase_receipt):
    doc = _get_submitted_receipt(purchase_receipt)
    if doc.custom_foodpanda_price_update_status == "Updated":
        return {"status": "Updated"}

    _require_price_update_permission()

    if not doc.branch:
        frappe.throw(_("This receipt has no Branch set - cannot update a Foodpanda price list."))

    price_list = get_or_create_branch_foodpanda_price_list(doc.branch)
    default_selling_price_list = (
        frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
    )

    for row in doc.items:
        fp_price = flt(row.custom_fp_price)
        has_existing = bool(
            frappe.db.get_value(
                "Item Price", {"item_code": row.item_code, "price_list": price_list, "selling": 1}, "name"
            )
        )

        if fp_price <= 0:
            if has_existing:
                # Later receipt with no FP Price entered - leave the existing
                # Foodpanda price untouched rather than overwriting it with 0.
                continue
            # First-ever Foodpanda price for this item, no FP Price given
            # yet - seed it from the item's Standard Selling rate (initial
            # setup only; every later receipt keeps whatever is already
            # there).
            fp_price = flt(
                frappe.db.get_value(
                    "Item Price",
                    {"item_code": row.item_code, "price_list": default_selling_price_list, "selling": 1},
                    "price_list_rate",
                )
            )
            if fp_price <= 0:
                continue

        # Flat FP-Price-as-rate for now, per explicit product decision - a
        # configurable rate/markup formula is future work, not built here.
        upsert_item_price(
            row.item_code,
            price_list,
            purchase_receipt=doc.name,
            branch=doc.branch,
            rate=fp_price,
            mrp=fp_price,
        )

    frappe.db.set_value("Purchase Receipt", doc.name, "custom_foodpanda_price_update_status", "Updated")
    return {"status": "Updated"}


@frappe.whitelist()
def skip_foodpanda_price_update(purchase_receipt):
    frappe.db.set_value(
        "Purchase Receipt", purchase_receipt, "custom_foodpanda_price_update_status", "Skipped"
    )
    return {"status": "Skipped"}
