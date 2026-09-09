"""Import shipped Insights workbooks onto the current site (idempotent)."""


_AIMATIC_TEMPLATE_ORDER = {
	"aimatic/basket_relevance": 90,
	"aimatic/owner_flash": 100,
}


def _template_sort_key(template_name: str) -> tuple[int, int, str]:
	if not template_name.startswith("aimatic/"):
		return (0, 0, template_name)
	return (1, _AIMATIC_TEMPLATE_ORDER.get(template_name, 10), template_name)


def import_workbook_templates():
	"""Create or refresh org-shared copies of Insights workbook templates.

	Aimatic templates are force-updated in place. Bundled Insights templates are
	imported once and left alone if already present. The native dashboard list is
	sorted by dashboard creation time, so the Aimatic CEO dashboard is imported
	last and appears first after refresh.
	"""
	import frappe
	from insights.api.templates import (
		create_workbook_from_template,
		get_template_names,
		update_workbook_from_template,
	)

	frappe.set_user("Administrator")
	results = []
	for template_name in sorted(get_template_names(), key=_template_sort_key):
		if template_name.startswith("aimatic/"):
			result = update_workbook_from_template(template_name)
		else:
			result = create_workbook_from_template(template_name)
		results.append({"template": template_name, **result})
	return results
