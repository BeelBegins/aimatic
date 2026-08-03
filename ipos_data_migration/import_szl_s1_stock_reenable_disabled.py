"""One-off follow-up to import_szl_s1_stock.py: 7 pre-existing disabled Items
(discontinued 'X Off' crockery variants, copied over from siezal's catalog)
were deliberately skipped by that script's main run because ERPNext refuses
a Stock Entry against a disabled Item. Per explicit decision 2026-08-02, they
do have real physical Onhand at S1 right now, so: re-enable them, then post
their opening stock in one small separate Material Receipt (same Temporary
Opening treatment as the main run, so it still nets correctly against the
opening vendor balances). Qty/rate values are exactly what the main run
already computed and logged before skipping them -- see its console output
('SKIPPED DISABLED ITEM (positive)' lines).

Run via bench console (same convention as the other scripts here):
    exec(open("apps/aimatic/ipos_data_migration/import_szl_s1_stock_reenable_disabled.py").read(), globals())
"""

import frappe

TARGET_SITE = "szl"
WAREHOUSE = "S1 - Ghouri Town VIP - SSM"
POSTING_DATE = "2026-08-02"
TAG = "S1-ONHAND-IMPORT-2026-08-02 reenabled-disabled-items"

# From import_szl_s1_stock.py's own console output (2026-08-02 run).
ROWS = [
    {"item_code": "STO-ITEM-2026-15256", "qty": 67.0, "rate": 277.918},
    {"item_code": "STO-ITEM-2026-15257", "qty": 43.0, "rate": 174.961},
    {"item_code": "STO-ITEM-2026-15258", "qty": 47.0, "rate": 149.092},
    {"item_code": "STO-ITEM-2026-15259", "qty": 15.0, "rate": 129.378},
    {"item_code": "STO-ITEM-2026-15260", "qty": 5.0, "rate": 238.8},
    {"item_code": "STO-ITEM-2026-15261", "qty": 13.0, "rate": 540.0},
    {"item_code": "STO-ITEM-2026-15262", "qty": 16.0, "rate": 534.062},
]


def get_branch_and_cost_center():
    branch = frappe.get_cached_value("Warehouse", WAREHOUSE, "custom_branch")
    cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
    return branch, cost_center


def get_temp_opening_account():
    company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
    return frappe.db.get_value(
        "Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
    )


def run():
    if frappe.local.site != TARGET_SITE:
        frappe.throw(f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'.")

    if frappe.db.exists("Stock Entry", {"remarks": TAG}):
        print("Already run -- Stock Entry with this tag already exists. Nothing to do.")
        return

    for row in ROWS:
        if frappe.db.get_value("Item", row["item_code"], "disabled"):
            frappe.db.set_value("Item", row["item_code"], "disabled", 0)
            print(f"Re-enabled {row['item_code']}")
    frappe.db.commit()

    branch, cost_center = get_branch_and_cost_center()
    temp_opening_account = get_temp_opening_account()
    if not temp_opening_account:
        frappe.throw("No 'Temporary Opening' account found.")

    entry = frappe.new_doc("Stock Entry")
    entry.stock_entry_type = "Material Receipt"
    entry.to_warehouse = WAREHOUSE
    entry.posting_date = POSTING_DATE
    entry.set_posting_time = 1
    entry.branch = branch
    entry.remarks = TAG

    for row in ROWS:
        entry.append(
            "items",
            {
                "item_code": row["item_code"],
                "qty": row["qty"],
                "t_warehouse": WAREHOUSE,
                "basic_rate": row["rate"],
                "valuation_rate": row["rate"],
                "allow_zero_valuation_rate": 0,
                "expense_account": temp_opening_account,
                "cost_center": cost_center,
                "branch": branch,
            },
        )

    entry.insert(ignore_permissions=True)
    entry.submit()
    frappe.db.commit()
    print(f"Created {entry.name} with {len(ROWS)} rows ({TAG})")


run()
