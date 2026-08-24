import json
import time

import frappe
import requests
from frappe import _

OFFICIAL_API_HOST = "https://foodpanda.partner.deliveryhero.io"
_TOKEN_PATH = "/v2/oauth/token"
_CACHE_PREFIX = "aimatic:foodpanda:token:"
_TOKEN_EXPIRY_SAFETY_MARGIN = 60
_MIN_TOKEN_TTL = 60
_FALLBACK_TOKEN_TTL = 300
_MAX_BACKOFF_SECONDS = 8


class FoodpandaAPIError(Exception):
	"""Raised for any Foodpanda Partner API call that didn't succeed after
	retries. `response_body` carries whatever JSON/text came back so a caller
	can surface a specific message without re-parsing the request."""

	def __init__(self, message, status_code=None, response_body=None):
		super().__init__(message)
		self.status_code = status_code
		self.response_body = response_body


def get_settings():
	settings = frappe.get_single("Foodpanda Settings")
	if not settings.enabled:
		frappe.throw(_("Foodpanda integration is not enabled"))
	if not settings.api_host:
		frappe.throw(_("Foodpanda Settings is missing the API Host"))
	if not settings.client_id:
		frappe.throw(_("Foodpanda Settings is missing the Client ID"))
	return settings


def get_chain_id(outlet=None, settings=None):
	"""Return the chain ID for this outlet, falling back to Settings default.

	Each Foodpanda Outlet can belong to a different chain. Partner API paths
	must use the outlet's chain, not a site-wide value.
	"""
	chain_id = ""
	if outlet is not None:
		raw = getattr(outlet, "chain_id", None)
		if isinstance(raw, str):
			chain_id = raw.strip()
	if not chain_id:
		settings = settings or get_settings()
		raw = getattr(settings, "chain_id", None)
		if isinstance(raw, str):
			chain_id = raw.strip()
	if not chain_id:
		if outlet is not None:
			frappe.throw(_("Foodpanda Outlet is missing the Chain ID"))
		frappe.throw(_("Foodpanda Settings is missing the Default Chain ID"))
	return chain_id


def _token_cache_key(client_id):
	return f"{_CACHE_PREFIX}{client_id}"


def _redact_sensitive(value):
	if isinstance(value, dict):
		return {
			key: "[redacted]"
			if key.lower() in {"access_token", "client_secret", "token"}
			else _redact_sensitive(item)
			for key, item in value.items()
		}
	if isinstance(value, list):
		return [_redact_sensitive(item) for item in value]
	return value


def _safe_body(response, sensitive_values=None):
	try:
		return _redact_sensitive(response.json())
	except Exception:
		raw_response = response.text[:2000]
		for sensitive_value in sensitive_values or ():
			if sensitive_value:
				raw_response = raw_response.replace(str(sensitive_value), "[redacted]")
		return {"raw_response": raw_response}


def _fetch_access_token(settings):
	client_secret = settings.get_password("client_secret", raise_exception=False)
	if not client_secret:
		frappe.throw(_("Foodpanda Settings is missing the Client Secret"))

	try:
		response = requests.post(
			f"{settings.api_host.rstrip('/')}{_TOKEN_PATH}",
			data={
				"client_id": settings.client_id,
				"client_secret": client_secret,
				"grant_type": "client_credentials",
			},
			timeout=int(settings.request_timeout or 30),
			verify=bool(settings.verify_ssl),
		)
	except requests.RequestException as error:
		raise FoodpandaAPIError(
			f"Foodpanda token request could not connect to {settings.api_host}: {error.__class__.__name__}"
		) from error

	if response.status_code != 200:
		# response_body may echo request fields back - never client_secret,
		# Foodpanda's token endpoint doesn't echo it, but keep this call site
		# as the one place a leak could happen so it stays easy to audit.
		raise FoodpandaAPIError(
			f"Foodpanda token request failed with HTTP {response.status_code}",
			status_code=response.status_code,
			response_body=_safe_body(response, sensitive_values=(client_secret,)),
		)

	try:
		data = response.json()
	except (TypeError, ValueError) as error:
		raise FoodpandaAPIError("Foodpanda token response was not valid JSON") from error
	token = data.get("access_token")
	if not token:
		raise FoodpandaAPIError(
			"Foodpanda token response had no access_token",
			response_body=_safe_body(response),
		)

	return token, int(data.get("expires_in") or 0)


def get_access_token(settings=None, force_refresh=False):
	settings = settings or get_settings()
	cache_key = _token_cache_key(settings.client_id)

	if not force_refresh:
		cached = frappe.cache.get_value(cache_key)
		if cached:
			return cached.decode() if isinstance(cached, bytes) else cached

	token, expires_in = _fetch_access_token(settings)
	ttl = max(expires_in - _TOKEN_EXPIRY_SAFETY_MARGIN, _MIN_TOKEN_TTL) if expires_in else _FALLBACK_TOKEN_TTL
	frappe.cache.set_value(cache_key, token, expires_in_sec=ttl)
	return token


def request(method, path, settings=None, **kwargs):
	"""Generic authenticated call against the Foodpanda Partner API.

	Retries on 429/5xx with capped exponential backoff up to
	settings.maximum_retries (fbr_pos defines equivalent fields but never
	implements the retry loop - that gap isn't repeated here). A single 401
	invalidates the cached token and retries once, since the cached token may
	have been revoked/rotated server-side.
	"""
	settings = settings or get_settings()
	url = f"{settings.api_host.rstrip('/')}{path}"
	timeout = int(settings.request_timeout or 30)
	verify = bool(settings.verify_ssl)
	max_retries = max(int(settings.maximum_retries or 0), 0)
	base_headers = dict(kwargs.pop("headers", None) or {})

	token = get_access_token(settings)
	allow_token_retry = True
	attempt = 0

	while True:
		headers = dict(base_headers)
		headers["Authorization"] = f"Bearer {token}"
		try:
			response = requests.request(
				method, url, headers=headers, timeout=timeout, verify=verify, **kwargs
			)
		except requests.RequestException as error:
			raise FoodpandaAPIError(
				f"Foodpanda API call to {path} could not connect: {error.__class__.__name__}"
			) from error

		if response.status_code == 401 and allow_token_retry:
			token = get_access_token(settings, force_refresh=True)
			allow_token_retry = False
			continue

		if response.status_code == 429 or response.status_code >= 500:
			if attempt >= max_retries:
				raise FoodpandaAPIError(
					f"Foodpanda API call to {path} failed with HTTP "
					f"{response.status_code} after {attempt} retries",
					status_code=response.status_code,
					response_body=_safe_body(response),
				)
			time.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
			attempt += 1
			continue

		if not response.ok:
			raise FoodpandaAPIError(
				f"Foodpanda API call to {path} failed with HTTP {response.status_code}",
				status_code=response.status_code,
				response_body=_safe_body(response),
			)

		return response


def log_api_failure(title, context, error):
	"""defer_insert=True writes to Redis instead of the current SQL
	transaction, matching aimatic.fbr_pos.api.log_fbr_submission_failure -
	Foodpanda calls can happen inside a savepoint (order webhook handling)
	that may roll back, and a plain frappe.log_error here would vanish with
	it."""
	body = getattr(error, "response_body", None)
	response_json = json.dumps(body, indent=2, ensure_ascii=False, default=str) if body else "n/a"
	frappe.log_error(
		title=title,
		message=f"{context}\n\nError: {error}\n\nResponse:\n{response_json}",
		defer_insert=True,
	)
