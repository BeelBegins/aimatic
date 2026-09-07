import frappe
from frappe import _
from frappe.utils import get_url
from urllib.parse import quote, urlencode

from aimaticlearning.lms_learning.revision import PREFERRED_COURSE


def get_context(context):
	if frappe.session.user == "Guest":
		query = {}
		if frappe.form_dict.get("course"):
			query["course"] = frappe.form_dict.course
		if frappe.form_dict.get("learning_module"):
			query["learning_module"] = frappe.form_dict.learning_module
		target = "/learning-revision"
		if query:
			target += "?" + urlencode(query)
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(target, safe="")
		raise frappe.Redirect

	context.no_cache = 1
	context.show_sidebar = False
	context.title = _("Revision")
	context.course = frappe.form_dict.get("course") or PREFERRED_COURSE
	context.learning_module = frappe.form_dict.get("learning_module")
	context.canonical_url = get_url("/learning-revision")
