import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    """Give every Branch a second, Foodpanda-only selling Price List, and let
    one POS Profile per branch be explicitly flagged as that branch's Food
    Panda profile.

    Previously there was one single global "Foodpanda" Price List shared by
    every branch, and no field distinguished a Food Panda POS Profile from a
    normal one - apply_pos_profile_branch_price_list forced every branch POS
    Profile onto the branch's normal selling list on every save, silently
    reverting any manual Foodpanda assignment (the incident that prompted
    this patch). Real fix: an explicit custom_is_foodpanda_profile checkbox,
    and a per-branch Foodpanda list the same way the normal selling list
    already works - see get_or_create_branch_foodpanda_price_list.
    """
    create_custom_fields(
        {
            "Branch": [
                {
                    "fieldname": "default_foodpanda_price_list",
                    "label": "Default Foodpanda Price List",
                    "fieldtype": "Link",
                    "options": "Price List",
                    "read_only": 1,
                    "insert_after": "default_selling_price_list",
                    "description": (
                        "Branch-specific Foodpanda-only selling Price List, auto-created "
                        "and populated when the Branch is initialized. Not hand-edited."
                    ),
                },
            ],
            "POS Profile": [
                {
                    "fieldname": "custom_is_foodpanda_profile",
                    "label": "Food Panda Profile",
                    "fieldtype": "Check",
                    "default": "0",
                    "insert_after": "custom_terminal_id",
                    "description": (
                        "This POS Profile sells off its branch's Foodpanda Price List "
                        "instead of the branch's normal selling list."
                    ),
                },
            ],
        },
        update=False,
    )

    from aimatic.shelf_pricing.utils import (
        FOODPANDA_PRICE_LIST,
        get_or_create_branch_foodpanda_price_list,
    )

    # Existing Food Panda POS Profiles predate this checkbox and are only
    # identifiable by name - flag them once so future saves route correctly.
    foodpanda_profiles = [
        name
        for name in frappe.get_all("POS Profile", pluck="name")
        if "foodpanda" in name.lower().replace(" ", "")
    ]
    for name in foodpanda_profiles:
        frappe.db.set_value("POS Profile", name, "custom_is_foodpanda_profile", 1)

    branches_missing_list = frappe.get_all(
        "Branch",
        fields=["name"],
        filters={"default_foodpanda_price_list": ["in", ["", None]]},
    )

    # An existing site-wide "Foodpanda" Price List almost certainly already
    # belongs to whichever single Branch has been using it live via its Food
    # Panda POS Profile - only auto-link it when that's unambiguous (exactly
    # one Branch still missing this field), mirroring tax_formula_setup's
    # same "repair only when unambiguous, otherwise leave untouched"
    # precedent. Multi-branch sites get a fresh list created per branch
    # instead of guessing which one the old global list belongs to.
    if len(branches_missing_list) == 1 and frappe.db.exists("Price List", FOODPANDA_PRICE_LIST):
        frappe.db.set_value(
            "Branch",
            branches_missing_list[0].name,
            "default_foodpanda_price_list",
            FOODPANDA_PRICE_LIST,
        )
    else:
        for branch in branches_missing_list:
            get_or_create_branch_foodpanda_price_list(branch.name)

    # Repoint each flagged profile onto its own branch's Foodpanda list right
    # now, rather than waiting for the next manual save of that POS Profile.
    for name in foodpanda_profiles:
        branch = frappe.db.get_value("POS Profile", name, "branch")
        if not branch:
            continue
        price_list = frappe.db.get_value("Branch", branch, "default_foodpanda_price_list")
        if price_list:
            frappe.db.set_value("POS Profile", name, "selling_price_list", price_list)

    frappe.db.commit()
