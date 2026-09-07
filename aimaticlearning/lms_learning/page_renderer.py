import frappe
from frappe.website.page_renderers.redirect_page import RedirectPage
from frappe.website.page_renderers.template_page import TemplatePage

from aimaticlearning.lms_learning.statistics import can_view_statistics


ASSET_MARKER = "examic-study-learning-assets"
ASSET_TAGS = f'''<!-- {ASSET_MARKER} -->
<link rel="stylesheet" href="/assets/aimaticlearning/css/lms_learning.css?v=20260902-2">
<link rel="stylesheet" href="/assets/aimaticlearning/css/lms_kinnu.css?v=20260901-2">
<link rel="stylesheet" href="/assets/aimaticlearning/css/lms_soft_themes.css?v=20260902-6">
<link rel="stylesheet" href="/assets/aimaticlearning/css/lms_student_experience.css?v=20260907-1">
<link rel="stylesheet" href="/assets/aimaticlearning/css/examic_sessions.css?v=20260901-2">
<script defer src="/assets/aimaticlearning/js/lms_dom_observe.js?v=20260902-1"></script>
<script defer src="/assets/aimaticlearning/js/lms_learning.js?v=20260902-5"></script>
<script defer src="/assets/aimaticlearning/js/lms_soft_themes.js?v=20260902-6"></script>
<script defer src="/assets/aimaticlearning/js/lms_kinnu.js?v=20260902-1"></script>
<script defer src="/assets/aimaticlearning/js/lms_student_experience.js?v=20260907-1"></script>
<script defer src="/assets/aimaticlearning/js/examic_sessions.js?v=20260902-2"></script>'''


class AimaticLMSPageRenderer(TemplatePage):
	"""Decorate the LMS-owned SPA shell with Examic Study learner experience assets."""

	def can_render(self):
		return self.path == "_lms" and super().can_render()

	def render(self):
		if frappe.local.request.path.rstrip("/") == "/lms/statistics" and not can_view_statistics():
			frappe.flags.redirect_location = "/lms/courses"
			return RedirectPage("/lms/courses", 302).render()

		response = super().render()
		html = response.get_data(as_text=True)
		if ASSET_MARKER not in html:
			html = html.replace("</head>", f"{ASSET_TAGS}\n</head>", 1)
			response.set_data(html)
		return response
