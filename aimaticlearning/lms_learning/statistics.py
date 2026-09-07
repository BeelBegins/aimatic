"""Staff-only access wrappers for product-level LMS statistics."""

import frappe
from frappe import _


STATISTICS_ROLES = {"Moderator", "Course Creator", "Batch Evaluator", "System Manager"}


def can_view_statistics(user: str | None = None) -> bool:
	"""Return whether a user may see organisation-wide LMS analytics."""
	user = user or frappe.session.user
	return user != "Guest" and bool(STATISTICS_ROLES.intersection(frappe.get_roles(user)))


def _require_statistics_access() -> None:
	if not can_view_statistics():
		frappe.throw(_("You do not have permission to view product statistics."), frappe.PermissionError)


@frappe.whitelist()
def get_chart_details():
	_require_statistics_access()
	from lms.lms.api import get_chart_details as native_get_chart_details

	return native_get_chart_details()


@frappe.whitelist()
def get_chart_data(chart_name: str, timegrain: str = "Daily", from_date: str | None = None, to_date: str | None = None):
	_require_statistics_access()
	from lms.lms.utils import get_chart_data as native_get_chart_data

	return native_get_chart_data(chart_name, timegrain, from_date, to_date)


@frappe.whitelist()
def get_course_completion_data():
	_require_statistics_access()
	from lms.lms.utils import get_course_completion_data as native_get_course_completion_data

	return native_get_course_completion_data()


@frappe.whitelist(allow_guest=True)
def get_sidebar_settings():
	"""Keep the native sidebar intact but omit analytics for non-staff learners."""
	from lms.lms.api import get_sidebar_settings as native_get_sidebar_settings

	settings = native_get_sidebar_settings()
	if not settings:
		return settings
	if not can_view_statistics():
		settings["statistics"] = False
	if frappe.session.user != "Guest":
		pages = list(settings.get("web_pages") or [])
		if not any(
			str(page.get("route") or page.get("to") or "").strip("/") == "learning-revision"
			for page in pages
		):
			pages.insert(
				0,
				{
					"label": "Revision",
					"to": "learning-revision",
					"route": "learning-revision",
					"icon": "RefreshCcw",
					"name": "aimatic-revision",
				},
			)
		settings["web_pages"] = pages
	return settings
