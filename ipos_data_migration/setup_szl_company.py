"""Phase A of the szl multi-branch setup: Fiscal Year, Company, Chart of
Accounts reconciliation, Head Office Cost Center, Company defaults, and
disabling the generic ERPNext fallbacks this codebase's "no generic fallback"
branch policy already retires on siezal/hsm (see root CLAUDE.md).

Run via bench console (this directory's scripts are not an importable Python
package -- see setup_szl.md for the exact invocation):
    exec(open("apps/aimatic/ipos_data_migration/setup_szl_company.py").read())
    main()

Safe to re-run: every step is guarded by a frappe.db.exists()/get_value()
check before creating or changing anything.
"""

import types

import frappe

_REF_DATA_PATH = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/szl_reference_data.py"
_ref_ns = {}
exec(compile(open(_REF_DATA_PATH).read(), _REF_DATA_PATH, "exec"), _ref_ns)
ref = types.SimpleNamespace(**_ref_ns)


def get_or_create_fiscal_year():
    name = ref.FISCAL_YEAR["name"]
    if frappe.db.exists("Fiscal Year", name):
        print(f"Fiscal Year {name} already exists")
        return name

    doc = frappe.new_doc("Fiscal Year")
    doc.year = name
    doc.year_start_date = ref.FISCAL_YEAR["year_start_date"]
    doc.year_end_date = ref.FISCAL_YEAR["year_end_date"]
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created Fiscal Year {doc.name}")
    return doc.name


def get_or_create_warehouse_type(name):
    """ERPNext's Company.create_default_warehouses() creates a "Goods In
    Transit" warehouse with warehouse_type="Transit" -- a plain link, not
    auto-created. szl never ran the setup wizard (the usual place this
    record gets seeded), so it's missing here even though it exists on
    siezal; create it if needed before Company creation."""
    if frappe.db.exists("Warehouse Type", name):
        return name
    doc = frappe.new_doc("Warehouse Type")
    doc.name = name
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created Warehouse Type {doc.name}")
    return doc.name


def create_company():
    if frappe.db.exists("Company", ref.COMPANY_NAME):
        print(f"Company {ref.COMPANY_NAME} already exists")
        return ref.COMPANY_NAME

    get_or_create_warehouse_type("Transit")

    doc = frappe.new_doc("Company")
    doc.company_name = ref.COMPANY_NAME
    doc.abbr = ref.COMPANY_ABBR
    doc.default_currency = "PKR"
    doc.country = "Pakistan"
    doc.create_chart_of_accounts_based_on = "Standard Template"
    doc.chart_of_accounts = "Standard with Numbers"
    # Deliberately do NOT set frappe.local.flags.ignore_chart_of_accounts --
    # let Company.on_update() run its normal path (standard numbered CoA
    # template, 5 generic warehouses, Pakistan country fixtures, and the
    # unconditional "Main" cost center), reusing ERPNext's own tested tree
    # builder rather than hand-building NestedSet placement.
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created Company {doc.name}")
    return doc.name


def _get_group_account(company, account_name):
    return frappe.db.get_value(
        "Account", {"company": company, "account_name": account_name, "is_group": 1}
    )


def _get_leaf_account(company, account_name):
    return frappe.db.get_value(
        "Account", {"company": company, "account_name": account_name, "is_group": 0}
    )


def _create_leaf_account(company, account_name, parent_account, root_type, account_number=None, account_type=None):
    account = frappe.new_doc("Account")
    account.account_name = account_name
    account.company = company
    account.parent_account = parent_account
    account.root_type = root_type
    account.is_group = 0
    if account_number:
        account.account_number = account_number
    if account_type:
        account.account_type = account_type
    account.insert(ignore_permissions=True)
    return account.name


def reconcile_chart_of_accounts(company):
    """Create only what the standard template doesn't already provide: the 4
    non-template accounts and the 42 Indirect Expenses leaves siezal added by
    hand. Everything else in siezal's 119-account tree is expected to already
    exist from the standard "Standard with Numbers" template Company creation
    just built."""
    created = 0
    skipped = 0

    for entry in ref.NON_TEMPLATE_ACCOUNTS:
        try:
            if _get_leaf_account(company, entry["account_name"]):
                skipped += 1
                continue
            parent = _get_group_account(company, entry["parent_account_name"])
            if not parent:
                frappe.throw(
                    f"No group account '{entry['parent_account_name']}' found under {company} "
                    f"to attach '{entry['account_name']}' to."
                )
            _create_leaf_account(
                company,
                entry["account_name"],
                parent,
                entry["root_type"],
                account_number=entry.get("account_number"),
                account_type=entry.get("account_type"),
            )
            created += 1
            frappe.db.commit()
        except Exception as exc:
            frappe.db.rollback()
            print(f"FAILED non-template account {entry['account_name']} -> {exc}")

    indirect_expenses_parent = _get_group_account(company, ref.INDIRECT_EXPENSE_PARENT_NAME)
    if not indirect_expenses_parent:
        frappe.throw(f"No group account '{ref.INDIRECT_EXPENSE_PARENT_NAME}' found under {company}.")

    renamed = 0
    for account_name, account_number, account_type in ref.INDIRECT_EXPENSE_LEAVES:
        try:
            if _get_leaf_account(company, account_name):
                skipped += 1
                continue

            # Two of siezal's numbers (5208, 5217) are renames of accounts
            # ERPNext's own "Standard with Numbers" template already creates
            # under those numbers with different default names ("Office
            # Maintenance Expenses"/"Utility Expenses"), not net-new leaves --
            # rename the existing template account instead of creating a
            # colliding duplicate account_number.
            existing_by_number = frappe.db.get_value(
                "Account",
                {"company": company, "account_number": account_number, "is_group": 0},
            )
            if existing_by_number:
                new_full_name = f"{account_number} - {account_name} - {ref.COMPANY_ABBR}"
                if existing_by_number != new_full_name:
                    # rename_doc only renames the document name -- account_name
                    # is a separate stored field it does not sync automatically,
                    # so it's fixed explicitly below regardless of this branch.
                    frappe.rename_doc("Account", existing_by_number, new_full_name, force=True)
                    renamed += 1
                else:
                    skipped += 1
                if frappe.db.get_value("Account", new_full_name, "account_name") != account_name:
                    frappe.db.set_value("Account", new_full_name, "account_name", account_name)
                continue

            _create_leaf_account(
                company,
                account_name,
                indirect_expenses_parent,
                "Expense",
                account_number=account_number,
                account_type=account_type or None,
            )
            created += 1
            frappe.db.commit()
        except Exception as exc:
            frappe.db.rollback()
            print(f"FAILED indirect expense account {account_name} -> {exc}")

    if renamed:
        frappe.db.commit()
        print(f"Renamed {renamed} standard-template account(s) to match siezal's naming")

    print(f"CoA reconciliation: {created} created, {skipped} already present")


def create_head_office_cost_center(company):
    name = ref.HEAD_OFFICE_COST_CENTER_NAME
    full_name = f"{name} - {ref.COMPANY_ABBR}"
    if frappe.db.exists("Cost Center", full_name):
        print(f"Cost Center {full_name} already exists")
        return full_name

    root_cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 1, "parent_cost_center": ["is", "not set"]})
    if not root_cost_center:
        # Root Cost Center is a NestedSet root -- parent_cost_center may be
        # stored as empty string rather than NULL depending on how it was
        # created; fall back to "is_group=1 with no parent" resolved by lft.
        root_cost_center = frappe.db.get_value(
            "Cost Center", {"company": company, "is_group": 1}, order_by="lft asc"
        )
    if not root_cost_center:
        frappe.throw(f"No root group Cost Center found for {company}.")

    doc = frappe.new_doc("Cost Center")
    doc.cost_center_name = name
    doc.company = company
    doc.parent_cost_center = root_cost_center
    doc.is_group = 0
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created Cost Center {doc.name}")
    return doc.name


def set_company_defaults(company, head_office_cost_center):
    updates = {}
    for fieldname, account_name in ref.COMPANY_DEFAULTS.items():
        account = _get_leaf_account(company, account_name)
        if not account:
            print(f"WARNING: could not resolve account '{account_name}' for Company field '{fieldname}' -- skipped")
            continue
        updates[fieldname] = account

    updates["cost_center"] = head_office_cost_center
    updates["round_off_cost_center"] = head_office_cost_center
    updates["depreciation_cost_center"] = head_office_cost_center
    updates["valuation_method"] = "FIFO"
    updates["enable_perpetual_inventory"] = 1

    doc = frappe.get_doc("Company", company)
    changed = False
    for fieldname, value in updates.items():
        if doc.get(fieldname) != value:
            doc.set(fieldname, value)
            changed = True

    if changed:
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"Updated Company defaults on {company}")
    else:
        print(f"Company defaults on {company} already match")


def disable_generic_fallbacks(company):
    """Matches the 'no generic fallback' policy already applied to siezal/hsm
    (root CLAUDE.md): the ERPNext-default Main cost center and the generic
    Stores/Work In Progress/Finished Goods/Goods In Transit warehouses are
    dead weight for a retail business with no manufacturing and are disabled,
    never used."""
    main_cc = frappe.db.get_value("Cost Center", {"company": company, "cost_center_name": "Main"})
    if main_cc and not frappe.db.get_value("Cost Center", main_cc, "disabled"):
        frappe.db.set_value("Cost Center", main_cc, "disabled", 1)
        print(f"Disabled Cost Center {main_cc}")
    elif main_cc:
        print(f"Cost Center {main_cc} already disabled")

    for warehouse_name in ("Stores", "Work In Progress", "Finished Goods", "Goods In Transit"):
        warehouse = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": warehouse_name})
        if not warehouse:
            continue
        if not frappe.db.get_value("Warehouse", warehouse, "disabled"):
            frappe.db.set_value("Warehouse", warehouse, "disabled", 1)
            print(f"Disabled Warehouse {warehouse}")
        else:
            print(f"Warehouse {warehouse} already disabled")

    frappe.db.commit()


def main():
    # Suppress doc-event Notification alerts (e.g. core ERPNext's "Notification
    # for new fiscal year") while scripting this one-time setup -- matches how
    # frappe's own patches/migrate/install flows already avoid firing business
    # email alerts during system setup (see Document.run_notifications).
    previous_in_patch = frappe.flags.in_patch
    frappe.flags.in_patch = True
    try:
        get_or_create_fiscal_year()
        company = create_company()
        reconcile_chart_of_accounts(company)
        head_office_cost_center = create_head_office_cost_center(company)
        set_company_defaults(company, head_office_cost_center)
        disable_generic_fallbacks(company)
    finally:
        frappe.flags.in_patch = previous_in_patch
    print("Phase A (Company/CoA) complete.")
