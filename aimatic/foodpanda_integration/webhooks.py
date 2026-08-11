import hmac

import frappe
from frappe import _

AUTHORIZATION_HEADER = "Authorization"

_FOODPANDA_WEBHOOK_SUFFIXES = (
	"/api/method/aimatic.foodpanda_integration.api.foodpanda_order_webhook",
	"/api/method/aimatic.foodpanda_integration.api.foodpanda_catalog_webhook",
)


def _expected_webhook_secret():
	settings = frappe.get_single("Foodpanda Settings")
	return settings.get_password("webhook_secret", raise_exception=False)


def _authorization_forms(value):
	"""Exact header value plus Bearer / bare-token variants."""
	value = str(value or "").strip()
	if not value:
		return set()
	forms = {value}
	parts = value.split(" ", 1)
	if len(parts) == 2 and parts[0].lower() in {"bearer", "basic", "token"}:
		token = parts[1].strip()
		if token:
			forms.add(token)
			forms.add(f"Bearer {token}")
	else:
		forms.add(f"Bearer {value}")
	return forms


def authorization_matches_webhook_secret(provided, expected=None):
	"""True when the Authorization header matches Foodpanda Settings.

	Partner Portal stores a static WebhookKeyAuth value. Foodpanda may send it
	verbatim, or as ``Bearer <token>`` / ``Basic <token>``. Frappe's core auth
	treats any two-token Authorization header as OAuth/API-key login and returns
	401 before guest whitelisted methods run — see validate_foodpanda_webhook_auth.
	"""
	expected = expected if expected is not None else _expected_webhook_secret()
	for exp in _authorization_forms(expected):
		for prov in _authorization_forms(provided):
			if exp and prov and hmac.compare_digest(str(exp), str(prov)):
				return True
	return False


def verify_webhook_authorization(provided=None):
	"""Validate Foodpanda's static WebhookKeyAuth value.

	The Vendor Portal value may be an opaque token or a complete Basic auth
	value. Foodpanda sends it verbatim in Authorization, so no request-body
	HMAC is involved.
	"""
	expected = _expected_webhook_secret()
	provided = provided if provided is not None else frappe.get_request_header(AUTHORIZATION_HEADER)
	if not expected:
		frappe.throw(_("Foodpanda webhook secret is not configured"), frappe.PermissionError)
	if not provided:
		frappe.throw(_("Missing Foodpanda Authorization header"), frappe.PermissionError)
	if not authorization_matches_webhook_secret(provided, expected):
		frappe.throw(_("Foodpanda webhook authorization failed"), frappe.PermissionError)
	return True


def validate_foodpanda_webhook_auth():
	"""Auth hook: accept Foodpanda WebhookKeyAuth before Frappe rejects Bearer/Basic.

	Frappe ``validate_auth`` raises AuthenticationError when Authorization has
	two parts (e.g. ``Bearer <secret>``) and the session is still Guest. That
	blocks allow_guest Foodpanda webhooks even when the secret is correct.
	Matching the configured webhook secret sets a non-Guest user so the request
	reaches the whitelisted method; the method still re-checks the secret.
	"""
	request = getattr(frappe.local, "request", None)
	if not request:
		return
	path = (request.path or "").rstrip("/")
	if not any(path.endswith(suffix) for suffix in _FOODPANDA_WEBHOOK_SUFFIXES):
		return
	if frappe.session.user not in ("", "Guest"):
		return
	provided = frappe.get_request_header(AUTHORIZATION_HEADER)
	if not authorization_matches_webhook_secret(provided):
		return
	frappe.set_user("Administrator")
