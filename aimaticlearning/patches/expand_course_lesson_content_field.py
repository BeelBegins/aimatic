import frappe


def execute():
	"""Allow full chapter notes in Course Lesson.content on LMS sites."""
	if not frappe.db.exists("DocType", "Course Lesson"):
		return

	if frappe.db.exists(
		"Property Setter",
		{"doc_type": "Course Lesson", "field_name": "content", "property": "fieldtype"},
	):
		return

	frappe.make_property_setter(
		{
			"doctype": "Course Lesson",
			"fieldname": "content",
			"property": "fieldtype",
			"value": "Long Text",
			"property_type": "Select",
		},
		ignore_validate=True,
		is_system_generated=0,
	)
