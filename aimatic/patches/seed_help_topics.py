import frappe

from aimatic.help.seed_topics import SEED_TOPICS


def execute():
	"""Idempotent seed of curated Help Topic rows for the Desk Help float."""
	for topic in SEED_TOPICS:
		title = topic["title"]
		existing = frappe.db.get_value("Help Topic", {"title": title}, "name")
		if existing:
			doc = frappe.get_doc("Help Topic", existing)
			doc.update(
				{
					"module": topic["module"],
					"doctypes": topic.get("doctypes") or "",
					"tags": topic.get("tags") or "",
					"priority": topic.get("priority") or 100,
					"starter_questions": topic.get("starter_questions") or "",
					"body": topic["body"],
					"enabled": 1,
				}
			)
			doc.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{
					"doctype": "Help Topic",
					"title": title,
					"module": topic["module"],
					"doctypes": topic.get("doctypes") or "",
					"tags": topic.get("tags") or "",
					"priority": topic.get("priority") or 100,
					"starter_questions": topic.get("starter_questions") or "",
					"body": topic["body"],
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
