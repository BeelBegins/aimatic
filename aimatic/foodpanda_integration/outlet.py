import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from aimatic.foodpanda_integration import client
from aimatic.foodpanda_integration.client import FoodpandaAPIError

_OUTLET_STATUS_PATH = "/v2/chains/{chain_id}/vendors/{vendor_id}/status"

_STATUS_API_VALUES = {"Open": "OPEN", "Closed": "CLOSED_TODAY", "Busy": "CLOSED_UNTIL"}
_API_STATUS_VALUES = {"OPEN": "Open", "CLOSED_TODAY": "Closed", "CLOSED": "Closed", "CLOSED_UNTIL": "Busy"}


def push_outlet_status(outlet_name, status, reason=None, closed_until=None):
	if status not in _STATUS_API_VALUES:
		frappe.throw(_("Status must be one of Open, Closed, or Busy"))

	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	settings = client.get_settings()
	payload = {"status": _STATUS_API_VALUES[status]}
	if status == "Busy":
		reason = reason or "TOO_BUSY_KITCHEN"
		closed_until = closed_until or add_to_date(now_datetime(), minutes=30).isoformat()
	if reason:
		payload["closed_reason"] = reason
	if closed_until:
		payload["closed_until"] = closed_until

	try:
		client.request(
			"PUT",
			_OUTLET_STATUS_PATH.format(
				chain_id=client.get_chain_id(outlet, settings=settings), vendor_id=outlet.vendor_id
			),
			settings=settings,
			json=payload,
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(f"Foodpanda outlet status push failed: {outlet_name}", str(payload), error)
		frappe.db.set_value("Foodpanda Outlet", outlet_name, "last_error", str(error))
		frappe.throw(_("Foodpanda outlet status update failed: {0}").format(str(error)))

	frappe.db.set_value(
		"Foodpanda Outlet",
		outlet_name,
		{"status_cache": status, "last_status_sync": now_datetime(), "last_error": ""},
	)
	return {"status": status}


def pull_outlet_status(outlet_name):
	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	settings = client.get_settings()

	try:
		response = client.request(
			"GET",
			_OUTLET_STATUS_PATH.format(
				chain_id=client.get_chain_id(outlet, settings=settings), vendor_id=outlet.vendor_id
			),
			settings=settings,
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(f"Foodpanda outlet status pull failed: {outlet_name}", outlet_name, error)
		frappe.db.set_value("Foodpanda Outlet", outlet_name, "last_error", str(error))
		frappe.throw(_("Foodpanda outlet status lookup failed: {0}").format(str(error)))

	remote_status = _API_STATUS_VALUES.get((response.json() or {}).get("status"), "Unknown")
	frappe.db.set_value(
		"Foodpanda Outlet",
		outlet_name,
		{"status_cache": remote_status, "last_status_sync": now_datetime(), "last_error": ""},
	)
	return {"status": remote_status}
