import re

import frappe
from frappe import _

NTN_PATTERN = re.compile(r"^[A-Za-z0-9]{7}$")


def validate_supplier_ntn(doc, method=None):
    if doc.tax_withholding_group == "Filers":
        value = doc.tax_id or ""
        if not NTN_PATTERN.match(value):
            frappe.throw(
                _(
                    "A valid 7-character NTN (letters/numbers, no dashes) is required in "
                    "Tax ID/NTN for suppliers in the Filers tax withholding group."
                )
            )

    doc.tax_ntn = doc.tax_id
