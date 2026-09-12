"""One-off: make Payment Entry.cost_center a required field.

Root cause of the szl cost-centre gaps found 2026-09-12: `cost_center` on
Payment Entry is optional (reqd=0) with nothing auto-filling it for manual
back-office (supplier cheque/bank) payments, unlike POS cash sales which get
it stamped in code from the terminal's profile. Two accountants
(mzaman@aimatic.tech, smir@aimatic.tech) occasionally saved without picking
one (~2-3% of their entries) - a data-entry slip on an optional field, not a
workflow bug, but worth closing off going forward.

Does not touch Frappe/ERPNext core: creates a Property Setter (the standard
customization mechanism), then exported by hand into
aimatic/fixtures/property_setter.json so it ships wherever aimatic installs.

Run:
  bench --site szl execute aimatic.ops_require_payment_entry_cost_center_20260912.run
"""

from __future__ import annotations

import frappe


def run():
    frappe.make_property_setter(
        {
            "doctype": "Payment Entry",
            "fieldname": "cost_center",
            "property": "reqd",
            "value": 1,
            "property_type": "Check",
        }
    )
    frappe.db.commit()
    return frappe.db.get_value(
        "Property Setter",
        {"doc_type": "Payment Entry", "field_name": "cost_center", "property": "reqd"},
        ["name", "value"],
        as_dict=True,
    )
