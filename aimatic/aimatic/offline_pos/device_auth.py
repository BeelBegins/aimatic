import hmac

import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from frappe.utils.data import sha256_hash

ANDROID_OAUTH_APP_NAME = "Aimatic POS Android"
DEVICE_ID_HEADER = "X-Aimatic-Device-ID"
DEVICE_TOKEN_HEADER = "X-Aimatic-Device-Token"


def hash_device_token(token):
	return sha256_hash(token or "")


def _audit_device_failure(user, hardware_id, pos_profile, status):
	"""Persist authentication failures before raising. Auth hooks run before
	endpoint mutations, so committing this isolated audit cannot commit business
	data from the requested operation."""
	if user and not frappe.db.exists("User", user):
		user = None
	frappe.get_doc({
		"doctype": "POS Device Audit Log",
		"user": user,
		"hardware_id": hardware_id,
		"pos_profile": pos_profile,
		"status": status,
		"created_at": now_datetime(),
	}).insert(ignore_permissions=True)
	frappe.db.commit()


def validate_device_proof(hardware_id=None, device_token=None):
	hardware_id = hardware_id or frappe.get_request_header(DEVICE_ID_HEADER)
	device_token = device_token or frappe.get_request_header(DEVICE_TOKEN_HEADER)
	user = frappe.session.user if frappe.session.user not in (None, "", "Guest") else None

	if not hardware_id or not device_token:
		_audit_device_failure(user, hardware_id, None, "device_proof_missing")
		frappe.throw(_("Android POS device authentication is required"), frappe.AuthenticationError)

	device = frappe.db.get_value(
		"POS Device",
		hardware_id,
		["enabled", "pos_profile", "device_token_hash"],
		as_dict=True,
	)
	if not device:
		_audit_device_failure(user, hardware_id, None, "unknown_device")
		frappe.throw(_("This device is not enrolled"), frappe.AuthenticationError)

	expected_hash = device.device_token_hash or ""
	provided_hash = hash_device_token(device_token)
	if not expected_hash or not hmac.compare_digest(expected_hash, provided_hash):
		_audit_device_failure(user, hardware_id, device.pos_profile, "device_proof_invalid")
		frappe.throw(_("Android POS device authentication is invalid"), frappe.AuthenticationError)

	if not cint(device.enabled):
		_audit_device_failure(user, hardware_id, device.pos_profile, "device_disabled")
		frappe.throw(_("This device has been disabled"), frappe.AuthenticationError)

	frappe.local.aimatic_pos_hardware_id = hardware_id
	frappe.local.aimatic_pos_profile = device.pos_profile
	return device.pos_profile


def _android_client_id():
	return frappe.db.get_value("OAuth Client", {"app_name": ANDROID_OAUTH_APP_NAME}, "name")


def _client_for_bearer_token(access_token):
	if not access_token:
		return None
	return frappe.db.get_value("OAuth Bearer Token", access_token, "client")


def _is_android_token_request(client_id):
	request = getattr(frappe.local, "request", None)
	return bool(
		request
		and request.path.endswith("/frappe.integrations.oauth2.get_token")
		and client_id
		and client_id == _android_client_id()
	)


def validate_android_pos_device_auth():
	"""Frappe auth hook: bind all Android POS Bearer/resource requests and
	token exchanges to an enabled, enrolled device. Other OAuth clients,
	Electron token auth, cookie sessions, and guest enrollment are untouched."""
	request = getattr(frappe.local, "request", None)
	if not request:
		return

	authorization = frappe.get_request_header("Authorization", "").split(" ", 1)
	if len(authorization) == 2 and authorization[0].lower() == "bearer":
		access_token = authorization[1]
		if _client_for_bearer_token(access_token) != _android_client_id():
			return
		try:
			validate_device_proof()
		except frappe.AuthenticationError:
			frappe.db.set_value("OAuth Bearer Token", access_token, "status", "Revoked")
			frappe.db.commit()
			raise
		return

	client_id = frappe.form_dict.get("client_id") if getattr(frappe.local, "form_dict", None) else None
	if _is_android_token_request(client_id):
		try:
			validate_device_proof()
		except frappe.AuthenticationError:
			refresh_token = frappe.form_dict.get("refresh_token")
			if refresh_token:
				frappe.db.set_value(
					"OAuth Bearer Token",
					{"refresh_token": refresh_token},
					"status",
					"Revoked",
				)
				frappe.db.commit()
			raise
