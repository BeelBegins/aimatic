import frappe


DOCTYPE = "Item"
POS_ROLES = ("POS User", "POS Supervisor")
PERMISSION_FIELDS = (
    "permlevel",
    "role",
    "if_owner",
    "select",
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "mask",
    "report",
    "export",
    "import",
    "share",
    "print",
    "email",
    "impersonate",
)


def _target_rows():
    """Every Custom DocPerm row Item should end up with: the doctype's own
    standard DocPerm set (Item Manager/Stock Manager/etc - silently lost the
    moment any Custom DocPerm exists for a doctype, see hooks.py's comment on
    the POS User/POS Supervisor fixture block) plus the two POS role
    read+export grants.
    """
    rows = []

    for permission in frappe.get_all("DocPerm", filters={"parent": DOCTYPE}, fields=list(PERMISSION_FIELDS)):
        row = {field: (permission.get(field) or 0) for field in PERMISSION_FIELDS}
        row["role"] = permission.role
        rows.append(row)

    for role in POS_ROLES:
        row = {field: 0 for field in PERMISSION_FIELDS}
        row.update({"role": role, "permlevel": 0, "read": 1, "export": 1})
        rows.append(row)

    return rows


def _row_key(row):
    return (row["role"], row["permlevel"])


def execute():
    """Reconcile Item's Custom DocPerm rows to the target set by content, not
    by name.

    Custom DocPerm's own autoname is "hash", which makes a name-based
    delete-then-recreate unreliable to reason about across repeated bench
    migrate / fixture-sync runs. Comparing (role, permlevel) plus every
    permission flag directly against what already exists sidesteps that
    entirely: matching rows are left alone or corrected in place, stray/
    duplicate rows are removed, and only genuinely missing rows are inserted.
    Migration patches execute once per site, but this repair is also called by
    the fresh-install and test bootstrap paths, so every invocation must
    converge safely.
    """
    target_by_key = {_row_key(row): row for row in _target_rows()}

    existing = frappe.get_all(
        "Custom DocPerm",
        filters={"parent": DOCTYPE},
        fields=["name", *PERMISSION_FIELDS],
    )

    seen_keys = set()

    for row in existing:
        key = _row_key(row)
        target = target_by_key.get(key)

        if target is None or key in seen_keys:
            # Doesn't belong in the target set, or a duplicate of a key
            # already reconciled from an earlier row.
            frappe.delete_doc("Custom DocPerm", row.name, ignore_permissions=True, force=1)
            continue

        seen_keys.add(key)

        if any((row.get(field) or 0) != (target.get(field) or 0) for field in PERMISSION_FIELDS):
            frappe.db.set_value("Custom DocPerm", row.name, target, update_modified=False)

    for key, target in target_by_key.items():
        if key not in seen_keys:
            frappe.get_doc({"doctype": "Custom DocPerm", "parent": DOCTYPE, **target}).insert(
                ignore_permissions=True
            )

    frappe.clear_cache(doctype=DOCTYPE)
    frappe.db.commit()
