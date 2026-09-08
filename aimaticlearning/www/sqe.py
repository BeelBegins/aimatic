import frappe

from aimaticlearning.lms_learning.sqe_pathway import FLK1_SUBJECTS, FLK2_SUBJECTS


def get_context(context):
	context.no_cache = 1
	context.title = "Examic Study | Focused SQE Preparation"
	context.meta_description = (
		"Prepare, practise and perform with structured SQE study notes, chapter MCQs, "
		"flashcards and realistic module assessments."
	)
	context.body_class = "sqe-public-page"
	# Hardcoded, not frappe.utils.get_url(): this site answers on lms.aimatic.tech,
	# examic.study, and www.examic.study alike, but examic.study is the public
	# brand and must be the one consistent canonical/OG host everywhere (matches
	# the JSON-LD provider URL below and aimaticlearning/www/sitemap.py).
	context.canonical_url = "https://examic.study/sqe"
	context.og_image = "https://examic.study/assets/aimaticlearning/images/examic-study-header.png"
	context.login_url = "/login"
	context.signup_url = "/login#signup"
	context.courses_url = "/lms/courses"
	context.flk1_courses = _get_courses(FLK1_SUBJECTS)
	context.flk2_courses = _get_courses(FLK2_SUBJECTS)
	context.viewer = _get_viewer_state()
	context.course_schema = _get_course_schema(context.flk1_courses + context.flk2_courses)
	return context


def _get_course_schema(courses: list[dict]) -> str:
	"""JSON-LD ItemList of Course entries so AI/search crawlers can read course
	facts directly, without executing the course-detail SPA's JavaScript."""
	site_url = "https://examic.study"
	provider = {"@type": "EducationalOrganization", "name": "Examic Study", "url": site_url}
	items = [
		{
			"@type": "ListItem",
			"position": idx,
			"item": {
				"@type": "Course",
				"name": course["title"],
				"description": course["summary"],
				"url": f"{site_url}{course['course_url']}",
				"provider": provider,
			},
		}
		for idx, course in enumerate(courses, start=1)
	]
	schema = {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": items}
	return frappe.as_json(schema)


def _get_courses(subjects) -> list[dict]:
	"""Return published subjects in the pathway's assessment order."""
	courses = []
	for subject in subjects:
		if not frappe.db.get_value("LMS Course", subject["course"], "published"):
			continue
		course = frappe.get_doc("LMS Course", subject["course"])
		courses.append(
			{
				"name": course.name,
				"title": course.title,
				"summary": course.short_introduction or subject["summary"],
				"course_url": f"/lms/courses/{course.name}",
				"activity_label": _activity_label(course.name),
			}
		)
	return courses


def _activity_label(course_name: str) -> str:
	"""Reflect what is actually live for this course, not a hardcoded guess."""
	has_mcqs = bool(
		frappe.db.sql(
			"""
			SELECT 1
			FROM `tabChapter Reference` cr
			JOIN `tabLesson Reference` lr ON lr.parent = cr.chapter
			JOIN `tabCourse Lesson` cl ON cl.name = lr.lesson
			WHERE cr.parent=%s AND cl.quiz_id IS NOT NULL AND cl.quiz_id != ''
			LIMIT 1
			""",
			(course_name,),
		)
	)
	learning_module = frappe.db.get_value("Learning Module Config", {"lms_course": course_name}, "name")
	has_flashcards = bool(
		learning_module
		and frappe.db.exists("Learning Flashcard", {"learning_module": learning_module, "status": "Published"})
	)
	if has_mcqs and has_flashcards:
		return "Notes, practice and revision"
	if has_mcqs:
		return "Notes and chapter MCQs"
	return "Structured study notes"


def _get_viewer_state() -> dict:
	user = frappe.session.user
	if user == "Guest":
		return {"mode": "visitor"}

	enrolments = frappe.get_all(
		"LMS Enrollment",
		filters={"member": user, "docstatus": 0},
		fields=["course", "modified"],
		order_by="modified desc",
		limit_page_length=1,
	)
	if not enrolments:
		return {"mode": "new"}

	course_name = enrolments[0].course
	if not frappe.db.get_value("LMS Course", course_name, "published"):
		return {"mode": "new"}

	course = frappe.get_doc("LMS Course", course_name)
	total_lessons = sum(
		len(frappe.get_doc("Course Chapter", row.chapter).lessons or [])
		for row in course.chapters or []
	)
	completed = frappe.db.count(
		"LMS Course Progress",
		{"member": user, "course": course.name, "status": "Complete"},
	)
	completed = min(completed, total_lessons)
	progress = round((completed / total_lessons) * 100) if total_lessons else 0
	return {
		"mode": "student",
		"course_name": course.name,
		"course_title": course.title,
		"completed": completed,
		"total_lessons": total_lessons,
		"progress": progress,
		"course_url": f"/lms/courses/{course.name}",
	}
