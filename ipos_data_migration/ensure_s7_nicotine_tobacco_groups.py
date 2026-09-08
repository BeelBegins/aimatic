"""Approved S7 exception: create Item Group Nicotine (parent) + Tobacco (child).

Hard-target szl. Idempotent. Does not create Items.
Backup required before first live run.
"""

import frappe

TARGET_SITE = "szl"
PARENT_NAME = "Nicotine"
CHILD_NAME = "Tobacco"
ROOT = "All Item Groups"


def main():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")

	if not frappe.db.exists("Item Group", ROOT):
		frappe.throw(f"Missing root Item Group {ROOT}")

	created = []
	if frappe.db.exists("Item Group", PARENT_NAME):
		parent = frappe.get_doc("Item Group", PARENT_NAME)
		print(f"exists parent {parent.name} is_group={parent.is_group} parent={parent.parent_item_group}")
		if not parent.is_group:
			parent.is_group = 1
			parent.save(ignore_permissions=True)
			print("set Nicotine is_group=1")
	else:
		parent = frappe.new_doc("Item Group")
		parent.item_group_name = PARENT_NAME
		parent.parent_item_group = ROOT
		parent.is_group = 1
		parent.insert(ignore_permissions=True)
		created.append(parent.name)
		print(f"created parent {parent.name}")

	if frappe.db.exists("Item Group", CHILD_NAME):
		child = frappe.get_doc("Item Group", CHILD_NAME)
		print(f"exists child {child.name} is_group={child.is_group} parent={child.parent_item_group}")
		if child.parent_item_group != PARENT_NAME:
			child.parent_item_group = PARENT_NAME
			child.is_group = 0
			child.save(ignore_permissions=True)
			print("moved Tobacco under Nicotine")
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
			"select name, parent_item_group, is_group from `tabItem Group` where name in (%s, %s)",
			(PARENT_NAME, CHILD_NAME),
		)
	)
