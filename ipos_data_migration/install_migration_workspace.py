"""Install / refresh the Aimatic Migration workspace on the current site.

Loads the shipped module JSON and upserts Workspace + Aimatic home shortcut.
Hard-target: szl (or siezal for test). Does not create Items.

Run:
    bench --site szl console
    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/install_migration_workspace.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
"""

from __future__ import annotations

import json
from pathlib import Path

import frappe
from frappe.utils import now

ALLOWED_SITES = {"szl", "siezal"}
WORKSPACE_JSON = Path(
	"/home/nabeel/frappe-bench/apps/aimatic/aimatic/aimatic/workspace/migration/migration.json"
)


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site not in ALLOWED_SITES:
		frappe.throw(f"Refusing Migration workspace install on '{site}' (allowed: {sorted(ALLOWED_SITES)})")


def upsert_workspace():
	data = json.loads(WORKSPACE_JSON.read_text())
	name = data["name"]
	data["modified"] = now()
	if frappe.db.exists("Workspace", name):
		doc = frappe.get_doc("Workspace", name)
		# Replace child tables cleanly
		doc.update(
			{
				"title": data.get("title") or name,
				"label": data.get("label") or name,
				"icon": data.get("icon"),
				"module": data.get("module"),
				"parent_page": data.get("parent_page") or "",
				"public": data.get("public", 1),
				"sequence_id": data.get("sequence_id"),
				"content": data.get("content"),
				"is_hidden": data.get("is_hidden", 0),
			}
		)
		doc.set("shortcuts", data.get("shortcuts") or [])
		doc.set("links", data.get("links") or [])
		doc.set("roles", data.get("roles") or [])
		doc.save(ignore_permissions=True)
		print(f"Updated Workspace {doc.name}")
	else:
		doc = frappe.get_doc(data)
		doc.insert(ignore_permissions=True)
		print(f"Created Workspace {doc.name}")
	frappe.db.commit()
	return doc.name


def ensure_aimatic_home_shortcut():
	"""Add Migration tile on Aimatic home if missing."""
	if not frappe.db.exists("Workspace", "Aimatic"):
		print("Aimatic workspace missing — skip home shortcut")
		return
	doc = frappe.get_doc("Workspace", "Aimatic")
	labels = {s.label for s in doc.shortcuts}
	if "Migration" not in labels:
		doc.append(
			"shortcuts",
			{
				"label": "Migration",
				"type": "URL",
				"url": "/app/migration",
				"color": "Purple",
			},
		)
		# Patch content JSON to include the tile
		try:
			content = json.loads(doc.content or "[]")
		except Exception:
			content = []
		if not any(b.get("data", {}).get("shortcut_name") == "Migration" for b in content):
			# Insert after Price Check / Foodpanda block, before spacer
			insert_at = len(content)
			for i, block in enumerate(content):
				if block.get("type") == "spacer":
					insert_at = i
					break
			content.insert(
				insert_at,
				{
					"id": "aimShortcutMigration",
					"type": "shortcut",
					"data": {"shortcut_name": "Migration", "col": 4},
				},
			)
			doc.content = json.dumps(content)
		doc.flags.ignore_version = True
		doc._original_modified = frappe.db.get_value("Workspace", "Aimatic", "modified")
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		print("Added Migration shortcut on Aimatic home")
	else:
		print("Aimatic home already has Migration shortcut")


def move_source_into_folder():
	"""Keep S7 source next to result files under Home/Migrations/S7."""
	if not frappe.db.exists("File", "0c7a4cfbc9"):
		return
	f = frappe.get_doc("File", "0c7a4cfbc9")
	if f.folder != "Home/Migrations/S7":
		f.folder = "Home/Migrations/S7"
		f.save(ignore_permissions=True)
		frappe.db.commit()
		print("Moved source Itemonhand-S7 Store.xls -> Home/Migrations/S7")


def main():
	_assert_site()
	# Ensure folder exists (created by audit upload; recreate if needed)
	if not frappe.db.exists("File", {"is_folder": 1, "name": "Home/Migrations"}):
		from frappe.core.doctype.file.file import create_new_folder

		create_new_folder("Migrations", "Home")
	if not frappe.db.exists("File", {"is_folder": 1, "name": "Home/Migrations/S7"}):
		from frappe.core.doctype.file.file import create_new_folder

		create_new_folder("S7", "Home/Migrations")
	upsert_workspace()
	ensure_aimatic_home_shortcut()
	move_source_into_folder()
	print("Migration workspace ready: /app/migration")
	print("S7 files folder: /app/file/view/home/Migrations/S7")
