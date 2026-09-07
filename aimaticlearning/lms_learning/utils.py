import frappe
from frappe import _

from lms.lms.utils import has_course_instructor_role, has_moderator_role


def user_can_access_course(course: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Guest":
		return False
	if has_moderator_role(user) or has_course_instructor_role(user):
		return True
	return bool(
		frappe.db.exists("LMS Enrollment", {"member": user, "course": course, "docstatus": 0})
	)


def user_can_access_chapter_profile(chapter_profile: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	module = frappe.db.get_value("Learning Chapter Profile", chapter_profile, "learning_module")
	if not module:
		return False
	course = frappe.db.get_value("Learning Module Config", module, "lms_course")
	if not course:
		return False
	return user_can_access_course(course, user)


def throw_access_denied():
	frappe.throw(_("You do not have access to this learning content."), frappe.PermissionError)
