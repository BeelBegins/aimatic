import frappe

from aimaticlearning.lms_learning.enrollment import configure_lms_student_access


def execute():
	if "lms" not in frappe.get_installed_apps():
		return
	configure_lms_student_access()
