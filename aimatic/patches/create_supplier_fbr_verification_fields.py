import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    create_custom_fields(
        {
            "Supplier": [
                {
                    "fieldname": "custom_fbr_registration_status",
                    "label": "FBR Registration Status",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "insert_after": "tax_ntn",
                },
                {
                    "fieldname": "custom_fbr_registration_type",
                    "label": "FBR Registration Type",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "insert_after": "custom_fbr_registration_status",
                },
                {
                    "fieldname": "custom_fbr_atl_status",
                    "label": "FBR ATL Status",
                    "fieldtype": "Data",
                    "read_only": 1,
                    "insert_after": "custom_fbr_registration_type",
                },
                {
                    "fieldname": "custom_fbr_verified_on",
                    "label": "FBR Verified On",
                    "fieldtype": "Datetime",
                    "read_only": 1,
                    "insert_after": "custom_fbr_atl_status",
                },
                {
                    "fieldname": "custom_fbr_verification_message",
                    "label": "FBR Verification Message",
                    "fieldtype": "Small Text",
                    "read_only": 1,
                    "insert_after": "custom_fbr_verified_on",
                },
            ]
        },
        update=False,
    )
    frappe.db.commit()
