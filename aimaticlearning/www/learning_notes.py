import frappe

from aimaticlearning.lms_learning.protected_notes import get_notes_for_profile


def get_context(context):
	chapter_profile = frappe.form_dict.chapter_profile
	if not chapter_profile:
		frappe.throw("Chapter profile is required", frappe.ValidationError)

	notes = get_notes_for_profile(chapter_profile)
	context.no_cache = 1
	context.show_sidebar = False
	context.chapter_title = notes["chapter_title"]
	context.notes_html = notes["notes_html"]
	context.chapter_profile = chapter_profile
