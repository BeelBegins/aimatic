import frappe

DEFAULT_ROLES = ("Label Printing User", "Label Printing Manager")


def after_install():
    """Runs on a fresh `bench install-app aimatic` (unlike patches, which are
    marked as already-applied and skipped on fresh installs). Also called
    from the equivalent patches so existing sites get the same data on
    `bench migrate` after upgrading."""
    create_roles()
    create_default_templates()


def create_roles():
    for role_name in DEFAULT_ROLES:
        if frappe.db.exists("Role", role_name):
            continue

        frappe.get_doc({
            "doctype": "Role",
            "role_name": role_name,
            "desk_access": 1,
        }).insert(ignore_permissions=True)


def create_default_templates():
    if not frappe.db.exists("AIM Label Template", "Default Barcode Label - Roll"):
        frappe.get_doc({
            "doctype": "AIM Label Template",
            "template_name": "Default Barcode Label - Roll",
            "enabled": 1,
            "label_type": "Barcode Label",
            "print_mode": "Browser HTML",
            "paper_type": "Roll",
            "barcode_type": "Code128",
            "label_width_mm": 40,
            "label_height_mm": 25,
            "barcode_height_mm": 10,
            "row_gap_mm": 0,
            "column_gap_mm": 0,
            "margin_top_mm": 0,
            "margin_bottom_mm": 0,
            "margin_left_mm": 0,
            "margin_right_mm": 0,
            "font_family": "Arial, Helvetica, sans-serif",
            "item_name_font_size": 8,
            "item_code_font_size": 6.5,
            "price_font_size": 9,
            "show_item_code": 1,
            "show_item_name": 1,
            "show_barcode_text": 1,
            "show_price": 1,
        }).insert(ignore_permissions=True)

    if not frappe.db.exists("AIM Label Template", "Default Barcode Label - Roll 2 Column"):
        frappe.get_doc({
            "doctype": "AIM Label Template",
            "template_name": "Default Barcode Label - Roll 2 Column",
            "enabled": 1,
            "label_type": "Barcode Label",
            "print_mode": "Browser HTML",
            "paper_type": "Roll",
            "barcode_type": "Code128",
            "label_width_mm": 38.1,  # 1.5 in
            "label_height_mm": 27.94,  # 1.1 in
            "column_count": 2,
            "barcode_height_mm": 9,
            "row_gap_mm": 3,
            "column_gap_mm": 3,
            "font_family": "Arial, Helvetica, sans-serif",
            "item_name_font_size": 8,
            "item_code_font_size": 7,
            "price_font_size": 12,
            "show_item_code": 0,
            "show_item_name": 1,
            "show_barcode_text": 1,
            "show_price": 1,
            "show_company": 1,
        }).insert(ignore_permissions=True)

    if not frappe.db.exists("AIM Label Template", "Default Shelf Label - A4 3 Column"):
        frappe.get_doc({
            "doctype": "AIM Label Template",
            "template_name": "Default Shelf Label - A4 3 Column",
            "enabled": 1,
            "label_type": "Shelf Label",
            "print_mode": "Browser HTML",
            "paper_type": "A4",
            "barcode_type": "Code128",
            "page_width_mm": 210,
            "page_height_mm": 297,
            "column_count": 3,
            "row_gap_mm": 3,
            "column_gap_mm": 3,
            "margin_top_mm": 10,
            "margin_bottom_mm": 10,
            "margin_left_mm": 8,
            "margin_right_mm": 8,
            "barcode_height_mm": 12,
            "font_family": "Arial, Helvetica, sans-serif",
            "item_name_font_size": 10,
            "item_code_font_size": 7,
            "price_font_size": 11,
            "show_item_code": 1,
            "show_item_name": 1,
            "show_barcode_text": 1,
            "show_price": 1,
        }).insert(ignore_permissions=True)
