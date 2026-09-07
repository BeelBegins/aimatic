import frappe

from aimaticlearning.lms_learning.protected_notes import get_notes_for_profile


def get_context(context):
	chapter_profile = frappe.form_dict.chapter_profile
	learning_module = frappe.form_dict.learning_module
	if not chapter_profile and not learning_module:
		frappe.throw("chapter_profile or learning_module is required", frappe.ValidationError)

	context.no_cache = 1
	context.show_sidebar = False
	context.chapter_profile = chapter_profile
	context.learning_module = learning_module
	context.chapter_title = ""
	context.course_chapter = None
	context.rating_filter = (frappe.form_dict.get("rating") or "").strip().lower()
	if chapter_profile:
		profile = frappe.get_doc("Learning Chapter Profile", chapter_profile)
		context.chapter_title = profile.chapter_title
		context.learning_module = profile.learning_module
		context.course_chapter = profile.course_chapter
	elif learning_module:
		title = frappe.db.get_value("Learning Module Config", learning_module, "title") or "Course"
		rating = context.rating_filter
		if rating in ("hard", "good", "easy"):
			context.chapter_title = f"{title} · {rating.title()} cards"
		else:
			context.chapter_title = title
