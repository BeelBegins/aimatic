from aimatic.label_printing.setup import create_default_templates


def execute():
    # create_default_templates() is idempotent (checks frappe.db.exists per
    # template) - safe to call again to pick up the 2-column template added
    # after create_label_printing_default_templates already ran on this site.
    create_default_templates()
