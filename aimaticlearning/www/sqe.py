import frappe

from aimaticlearning.lms_learning.sqe_pathway import FLK1_SUBJECTS, FLK2_SUBJECTS


COURSE_NAME = "business-law-practice-blp"


def get_context(context):
	context.no_cache = 1
	context.title = "Examic Study | Focused SQE Preparation"
	context.meta_description = (
		"Prepare, practise and perform with structured SQE study notes, chapter MCQs, "
		"flashcards and realistic module assessments."
	)
	context.body_class = "sqe-public-page"
	context.canonical_url = frappe.utils.get_url("/sqe")
	context.login_url = "/login"
	context.signup_url = "/login#signup"
	context.courses_url = "/lms/courses"
	context.flk1_courses = _get_courses(FLK1_SUBJECTS)
	context.flk2_courses = _get_courses(FLK2_SUBJECTS)
	context.viewer = _get_viewer_state()
	return context


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
				"activity_label": "Notes, practice and revision"
				if course.name == COURSE_NAME
				else "Structured study notes",
			}
		)
	return courses


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
