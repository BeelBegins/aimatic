import hmac

import frappe
from frappe import _

AUTHORIZATION_HEADER = "Authorization"


def verify_webhook_authorization(provided=None):
	"""Validate Foodpanda's static WebhookKeyAuth value.

	The Vendor Portal value may be an opaque token or a complete Basic auth
	value. Foodpanda sends it verbatim in Authorization, so no request-body
	HMAC is involved.
	"""
	settings = frappe.get_single("Foodpanda Settings")
	expected = settings.get_password("webhook_secret", raise_exception=False)
	provided = provided if provided is not None else frappe.get_request_header(AUTHORIZATION_HEADER)
	if not expected:
		frappe.throw(_("Foodpanda webhook secret is not configured"), frappe.PermissionError)
	if not provided:
		frappe.throw(_("Missing Foodpanda Authorization header"), frappe.PermissionError)
	if not hmac.compare_digest(str(expected), str(provided)):
		frappe.throw(_("Foodpanda webhook authorization failed"), frappe.PermissionError)
	return True
