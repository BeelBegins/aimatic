import frappe


def execute():
	"""Force AI Integration Settings.enabled = 1 on every site right after the
	doctype is created by migrate.

	This is a Single doctype - Frappe only reliably applies a field's JSON
	"default" the moment it actually constructs the underlying document, which
	isn't guaranteed to happen automatically here. Since `enabled` is a kill
	switch for the whole AI Assistant (aimatic.ai), leaving it ambiguous could
	silently disable a feature that was working before this doctype existed -
	so this sets it explicitly rather than relying on the JSON default alone.
	Safe to re-run: always sets true, never toggles an admin's later choice to
	disable it back off (this patch only ever runs once per site anyway).
	"""
	frappe.db.set_single_value("AI Integration Settings", "enabled", 1)
