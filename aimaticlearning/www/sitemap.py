from urllib.parse import quote

import frappe
from frappe.utils import nowdate

no_cache = 1
base_template_path = "www/sitemap.xml"

SITE_URL = "https://examic.study"
STATIC_ROUTES = ["", "about", "contact", "sqe", "lms/courses"]


def get_context(context):
	today = nowdate()
	links = [
		{"loc": f"{SITE_URL}/{route}" if route else SITE_URL, "lastmod": today} for route in STATIC_ROUTES
	]

	courses = frappe.get_all(
		"LMS Course",
		filters={"published": 1},
		fields=["name", "modified"],
		order_by="modified desc",
	)
	for course in courses:
		links.append(
			{
				"loc": f"{SITE_URL}/lms/courses/{quote(course.name)}",
				"lastmod": course.modified.strftime("%Y-%m-%d"),
			}
		)

	return {"links": links}
