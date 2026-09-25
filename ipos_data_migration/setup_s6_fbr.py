"""Copy FBR Integration Settings for S6 - Khalid Block on live szl.

Copies the S4 row, sets branch and branch_code=1006, and leaves the
new row **disabled** with pos_id empty until a unique production POS ID is
set. Does not change S1/S4/S7 FBR rows.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/setup_s6_fbr.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import frappe

TARGET_SITE = "szl"
TEMPLATE = "Siezal Supermarket-S4 - Wallayat Complex"
NAME = "Siezal Supermarket-S6 - Khalid Block"
BRANCH = "S6 - Khalid Block"
BRANCH_CODE = "1006"
PROTECTED = {
    "Siezal Supermarket-S1 - Ghouri Town VIP",
    "Siezal Supermarket-S4 - Wallayat Complex",
    "Siezal Supermarket-S7 - Empire Heights",
}


def _assert_site():
    site = getattr(frappe.local, "site", None) or ""
    if site != TARGET_SITE:
        frappe.throw(f"Refusing S6 FBR setup: expected {TARGET_SITE}, got {site}")
    if not frappe.db.exists("FBR Integration Settings", TEMPLATE):
        frappe.throw(f"Missing template {TEMPLATE}")
    if not frappe.db.exists("Branch", BRANCH):
        frappe.throw(f"Missing Branch {BRANCH}")


def snapshot():
    rows = frappe.get_all(
        "FBR Integration Settings",
        fields=["name", "branch", "pos_id", "branch_code", "enabled"],
        order_by="name",
    )
    return [dict(r) for r in rows]


def main(apply: bool = False):
    _assert_site()
    before = snapshot()
    print({"site": TARGET_SITE, "apply": apply, "before": before})
    if not apply:
        print("DRY_RUN — no FBR row copied.")
        return before
    if frappe.db.exists("FBR Integration Settings", NAME):
        doc = frappe.get_doc("FBR Integration Settings", NAME)
        doc.branch = BRANCH
        doc.branch_code = BRANCH_CODE
        doc.enabled = 0
        if str(doc.pos_id or "") in {"176213", "184234", "196711"}:
            doc.pos_id = 100600
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        print("updated", NAME)
        print({"after": snapshot()})
        return snapshot()
    template = frappe.get_doc("FBR Integration Settings", TEMPLATE)
    doc = frappe.copy_doc(template)
    doc.name = NAME
    doc.branch = BRANCH
    doc.branch_code = BRANCH_CODE
    doc.enabled = 0
    doc.pos_id = 100600  # placeholder, not a live POS ID; leave disabled
    doc.insert(ignore_permissions=True, set_name=NAME)
    frappe.db.commit()
    after = snapshot()
    for row in after:
        if row["name"] in PROTECTED:
            orig = next(r for r in before if r["name"] == row["name"])
            if orig != row:
                frappe.throw(f"Protected FBR row changed: {row['name']}")
    print("created", NAME)
    print({"after": after})
    return after
