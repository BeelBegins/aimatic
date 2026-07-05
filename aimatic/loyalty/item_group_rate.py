import frappe
from frappe.utils import cint, flt
from frappe.utils.nestedset import get_ancestors_of


def get_item_group_loyalty_rate(item_group):
    """Loyalty points earned per Rs 100, cascading up the Item Group tree.

    Mirrors erpnext.stock.get_item_details._get_item_tax_template_from_item_group:
    walk the item's own group then its ancestors (nearest first) and return the
    first one that has an explicit rate configured, so a rate set on a parent
    group applies to all descendants unless a descendant overrides it —
    including an explicit override to 0.
    """
    if not item_group:
        return 0.0

    ancestors = get_ancestors_of("Item Group", item_group)
    for group in [item_group, *ancestors]:
        group_doc = frappe.get_cached_doc("Item Group", group)
        if cint(group_doc.get("custom_loyalty_rate_configured")):
            return flt(group_doc.get("custom_loyalty_points_rate"))

    return 0.0
