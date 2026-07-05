import frappe


def execute():
    """Add a DB index on Customer.mobile_no.

    offline_pos.customer_validation.validate_customer runs a duplicate-mobile_no
    lookup on every Customer save. Customer.mobile_no has no index in core
    ERPNext, so that lookup was a full table scan that gets slower as the
    Customer table grows. A Property Setter (search_index=1) is used instead of
    editing core so the index survives future schema migrations.
    """
    if not frappe.db.exists(
        "Property Setter",
        {"doc_type": "Customer", "field_name": "mobile_no", "property": "search_index"},
    ):
        frappe.make_property_setter(
            {
                "doctype": "Customer",
                "fieldname": "mobile_no",
                "property": "search_index",
                "value": 1,
                "property_type": "Check",
            }
        )

    frappe.db.add_index("Customer", ["mobile_no"])
    frappe.db.commit()
