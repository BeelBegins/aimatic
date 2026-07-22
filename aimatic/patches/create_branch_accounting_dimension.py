import frappe


def execute():
    """Create the missing Accounting Dimension master record for Branch.

    The `branch` Custom Field was fixture-shipped onto GL Entry and dozens of
    other doctypes, but the Accounting Dimension record that actually tells
    Frappe to copy that field into GL Entry (accounts_controller.get_gl_dict
    -> get_accounting_dimensions(), which reads frappe.get_all("Accounting
    Dimension", filters={"disabled": 0})) never existed on any site -- so
    `branch` has never once propagated into GL Entry, on any voucher type,
    despite being correctly populated on source documents/rows by
    branch_management.apply_branch_defaults. cost_center is unaffected by
    this gap since it's a native field hardcoded into the GL args dict at
    every posting call site in stock_controller.py/accounts_controller.py,
    not a custom accounting dimension.

    This patch only fixes it prospectively (new GL Entries from this point
    on). It does not backfill existing submitted GL Entries -- see the
    one-off backfill run recorded in CLAUDE.md for the correction already
    applied to szl/siezal/hsm.
    """
    if frappe.db.exists("Accounting Dimension", {"document_type": "Branch"}):
        return

    frappe.get_doc(
        {
            "doctype": "Accounting Dimension",
            "document_type": "Branch",
            "label": "Branch",
            "fieldname": "branch",
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
