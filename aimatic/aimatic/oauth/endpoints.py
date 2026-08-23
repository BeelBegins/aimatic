import json

import frappe
from frappe.oauth import generate_json_error_response
from oauthlib.oauth2 import FatalClientError, OAuth2Error
from oauthlib.openid.connect.core.endpoints.pre_configured import Server as WebApplicationServer

from aimatic.aimatic.oauth.validator import AimaticOAuthRequestValidator


def _get_aimatic_oauth_server():
	# Mirrors frappe.integrations.oauth2.get_oauth_server() exactly, swapping
	# in AimaticOAuthRequestValidator so refresh-token rotation/replay
	# detection (see validator.py) applies — this is the only reason this
	# function (and get_token below) exist; no other behavior differs from
	# core. Cached on frappe.local under a different attr than core's own
	# oauth_server so the two never collide if both are ever constructed in
	# the same request.
	if not getattr(frappe.local, "aimatic_oauth_server", None):
		frappe.local.aimatic_oauth_server = WebApplicationServer(AimaticOAuthRequestValidator())
	return frappe.local.aimatic_oauth_server


@frappe.whitelist(allow_guest=True)
def get_token(*args, **kwargs):
	"""Registered via override_whitelisted_methods for
	frappe.integrations.oauth2.get_token (see aimatic/hooks.py) — same
	contract as core's get_token, only the validator differs. Device-level
	revocation doesn't need any token bookkeeping here: every POS endpoint
	that matters calls require_active_device(hardware_id) on each request
	(see offline_pos.api), so a disabled device is rejected on its very next
	call regardless of whether its OAuth bearer token is still technically
	valid."""
	try:
		r = frappe.request
		_headers, body, _status = _get_aimatic_oauth_server().create_token_response(
			r.url, r.method, r.form, r.headers, frappe.flags.oauth_credentials
		)
		body = frappe._dict(json.loads(body))

		if body.error:
			frappe.local.response = body
			frappe.local.response["http_status_code"] = 400
			return

		frappe.local.response = body
		return

	except (FatalClientError, OAuth2Error) as e:
		return generate_json_error_response(e)
