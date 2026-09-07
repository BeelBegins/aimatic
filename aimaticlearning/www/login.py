"""Examic Study login page — delegate to Frappe login context, then apply LMS branding."""

from frappe.www.login import get_context as frappe_login_context

from aimaticlearning.lms_learning.enrollment import BRAND_NAME, LOGIN_MARK, is_lms_site

no_cache = True


def get_context(context):
	frappe_login_context(context)
	if not is_lms_site():
		return context
	context["logo"] = LOGIN_MARK
	context["app_name"] = BRAND_NAME
	context["full_width"] = 1
	return context
