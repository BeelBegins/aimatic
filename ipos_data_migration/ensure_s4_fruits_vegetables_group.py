"""Approved S4 exception: create Item Group "Fruits & Vegetables" (leaf).

User decision 2026-09-13: master SubCatName `FRUITES & VEGETABLES` (90 rows)
has no existing Item Group covering fresh produce — genuinely new territory,
not a spelling/alias fix like S4's other 3 subcategory gates. Approved as a
new leaf under the existing parent `Food Items` (which already holds
`Dry Fruits - Nuts` as a sibling leaf) — same shape as S7's approved
Nicotine (parent) / Tobacco (child) exception, except here the parent
already exists so only one new leaf is needed.

Idempotent. Does not create Items. Allows mock (siezal) or live (szl).
"""

import frappe

ALLOWED_SITES = ("siezal", "szl")
PARENT_NAME = "Food Items"
CHILD_NAME = "Fruits & Vegetables"


def main():
	site = getattr(frappe.local, "site", None) or ""
	if site not in ALLOWED_SITES:
		frappe.throw(f"Refusing: expected site in {ALLOWED_SITES}, got '{site}'")

	if not frappe.db.exists("Item Group", PARENT_NAME):
		frappe.throw(f"Missing expected parent Item Group {PARENT_NAME}")

	created = []
	if frappe.db.exists("Item Group", CHILD_NAME):
		child = frappe.get_doc("Item Group", CHILD_NAME)
		print(f"exists child {child.name} is_group={child.is_group} parent={child.parent_item_group}")
		if child.parent_item_group != PARENT_NAME:
			child.parent_item_group = PARENT_NAME
			child.is_group = 0
			child.save(ignore_permissions=True)
			print(f"moved {CHILD_NAME} under {PARENT_NAME}")
	else:
		child = frappe.new_doc("Item Group")
		child.item_group_name = CHILD_NAME
		child.parent_item_group = PARENT_NAME
		child.is_group = 0
		child.insert(ignore_permissions=True)
		created.append(child.name)
		print(f"created child {child.name}")

	frappe.db.commit()
	print("created", created)
	print(
		frappe.db.sql(
			"select name, parent_item_group, is_group from `tabItem Group` where name=%s",
			(CHILD_NAME,),
		)
	)
