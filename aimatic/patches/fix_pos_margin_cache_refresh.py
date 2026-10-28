"""Replace the global-date watermark that skips receipts closed later."""

import hashlib

import frappe

BEFORE_SHA256 = "8f356f0bb86b9f50b536111a9c3517092cafca22adf45aad6ef3fb29daf2fe66"
NEW_SCRIPT = 'result = frappe.call("aimatic.insights_workbooks.pos_margin_cache.refresh_missing")\nprint(result)\n'


def execute():
	if not frappe.db.exists("Server Script", "Refresh POS Gross Margin Cache"):
		return
	doc = frappe.get_doc("Server Script", "Refresh POS Gross Margin Cache")
	if doc.script == NEW_SCRIPT:
		return
	if hashlib.sha256((doc.script or "").encode()).hexdigest() != BEFORE_SHA256:
		frappe.throw("POS margin refresh script changed; review before updating it.")
	doc.script = NEW_SCRIPT
	doc.save(ignore_permissions=True)
