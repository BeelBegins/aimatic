import frappe

# Every doctype the 2026-07-19 fixture batch (see hooks.py's Custom DocPerm
# fixture, prefix "custom_docperm") added a bare POS User/POS Supervisor
# read grant to. Item and Item Price hit this same bug independently and
# were already repaired one at a time (repair_item_custom_docperms,
# repair_item_price_custom_docperms) - this patch covers the remaining
# doctypes from that same batch, found by auditing every doctype in it via
# frappe.permissions.get_valid_perms and comparing against each doctype's
# own standard DocPerm table on 2026-07-26: these seven came back showing
# *only* POS User/POS Supervisor, meaning every standard role (Accounts
# Manager, Sales Manager, System Manager, Stock Manager, etc, depending on
# the doctype) was silently wiped the moment the fixture's Custom DocPerm
# row was the first one ever created for that parent. Company, Mode of
# Payment, Branch, and Customer were audited too and are NOT in this list -
# they already had broader Custom DocPerm coverage before the 2026-07-19
# batch, so adding the POS grant there never triggered the "any Custom
# DocPerm wipes all standard DocPerm" failure mode.
DOCTYPES = (
	"POS Profile",
	"Sales Taxes and Charges Template",
	"Coupon Code",
	"Customer Group",
	"Territory",
	"Bin",
	"Print Format",
)
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


def _target_rows(doctype):
	"""Every Custom DocPerm row this doctype should end up with: its own
	standard DocPerm set, restored from the (untouched) DocPerm table, plus
	the read-only POS User/POS Supervisor grant the 2026-07-19 batch
	intended.
	"""
	rows = []

	for permission in frappe.get_all("DocPerm", filters={"parent": doctype}, fields=list(PERMISSION_FIELDS)):
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


def _repair(doctype):
	"""Reconcile a doctype's Custom DocPerm rows to the target set by
	content, not by name - see repair_item_custom_docperms for why (Custom
	DocPerm's own autoname is "hash", which makes a name-based
	delete-then-recreate unreliable across repeated bench migrate /
	fixture-sync runs).
	"""
	target_by_key = {_row_key(row): row for row in _target_rows(doctype)}

	existing = frappe.get_all(
		"Custom DocPerm",
		filters={"parent": doctype},
		fields=["name", *PERMISSION_FIELDS],
	)

	seen_keys = set()

	for row in existing:
		key = _row_key(row)
		target = target_by_key.get(key)

		if target is None or key in seen_keys:
			frappe.delete_doc("Custom DocPerm", row.name, ignore_permissions=True, force=1)
			continue

		seen_keys.add(key)

		if any((row.get(field) or 0) != (target.get(field) or 0) for field in PERMISSION_FIELDS):
			frappe.db.set_value("Custom DocPerm", row.name, target, update_modified=False)

	for key, target in target_by_key.items():
		if key not in seen_keys:
			frappe.get_doc({"doctype": "Custom DocPerm", "parent": doctype, **target}).insert(
				ignore_permissions=True
			)

	frappe.clear_cache(doctype=doctype)


def execute():
	for doctype in DOCTYPES:
		_repair(doctype)

	frappe.db.commit()
