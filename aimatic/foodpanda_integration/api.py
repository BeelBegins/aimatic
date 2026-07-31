import json

import frappe
from frappe import _
from frappe.utils import get_url, now_datetime

from aimatic.foodpanda_integration import (
	catalog,
	catalog_jobs,
	orders,
	outlet as outlet_module,
	pim_catalog,
	webhooks,
)
from aimatic.foodpanda_integration.client import FoodpandaAPIError

_ALLOWED_ROLES = {"System Manager", "Buying Price Control"}


def _require_permission():
	if not _ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(
			_("You need the Buying Price Control role to manage Foodpanda sync."),
			frappe.PermissionError,
		)


@frappe.whitelist(allow_guest=True)
def foodpanda_order_webhook():
	"""Inbound endpoint Foodpanda calls when an order is placed/updated.
	Verifies the static Authorization value, dedupes by foodpanda_order_id, and turns a new
	order into a draft Sales Order - see aimatic.foodpanda_integration.orders
	for the per-step logic and why the log row is committed before the
	Sales-Order-creation savepoint is opened.
	"""
	raw = frappe.request.get_data()
	webhooks.verify_webhook_authorization()

	try:
		payload = json.loads(raw)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid JSON payload"))

	foodpanda_order_id = payload.get("order_id")
	if not foodpanda_order_id:
		frappe.throw(_("Webhook payload is missing order_id"))

	remote_status = str(payload.get("status") or "RECEIVED").upper()
	existing = frappe.db.get_value(
		"Foodpanda Order Log",
		{"foodpanda_order_id": foodpanda_order_id},
		["name", "status", "sales_order"],
		as_dict=True,
	)
	if existing:
		local_status = {"CANCELLED": "Rejected", "DELIVERED": "Fulfilled"}.get(remote_status, existing.status)
		frappe.db.set_value("Foodpanda Order Log", existing.name, {
			"status": local_status,
			"remote_status": remote_status,
			"raw_payload": raw.decode("utf-8", errors="replace"),
		})
		return {"status": local_status, "sales_order": existing.sales_order, "duplicate": True}

	if remote_status != "RECEIVED":
		return {"status": "Ignored", "remote_status": remote_status, "reason": "Original order was not received"}
	outlet = orders.resolve_outlet(payload)

	log = frappe.get_doc(
		{
			"doctype": "Foodpanda Order Log",
			"foodpanda_order_id": foodpanda_order_id,
			"order_code": payload.get("order_code"),
			"outlet": outlet.name,
			"status": "Received",
			"remote_status": remote_status,
			"transport_type": payload.get("transport_type"),
			"raw_payload": raw.decode("utf-8", errors="replace"),
			"received_at": now_datetime(),
		}
	)
	log.insert(ignore_permissions=True)
	# Committed now so this record survives the rollback below - a crash or
	# rejected order still leaves an auditable, replayable row instead of
	# nothing at all (mirrors why aimatic.fbr_pos.api defers its own failure
	# logging around a savepoint that may roll back).
	frappe.db.commit()

	sp = "foodpanda_order_webhook"
	frappe.db.savepoint(sp)
	try:
		sales_order = orders.make_order_from_webhook(payload, outlet)
		frappe.db.release_savepoint(sp)
	except Exception as error:
		frappe.db.rollback(save_point=sp)
		log.db_set({"status": "Failed", "error": str(error)})
		try:
			orders.reject_order(outlet, foodpanda_order_id, reason=str(error)[:200])
		except FoodpandaAPIError:
			pass  # already logged inside reject_order
		return {"status": "Failed", "error": str(error)}

	log.db_set({"status": "Accepted", "sales_order": sales_order.name})

	return {"status": "Accepted", "sales_order": sales_order.name}


@frappe.whitelist(allow_guest=True)
def foodpanda_catalog_webhook():
	"""Assortment/catalog completion callback configured in Vendor Portal."""
	webhooks.verify_webhook_authorization()
	raw = frappe.request.get_data()
	try:
		payload = json.loads(raw)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid JSON payload"))
	return catalog_jobs.process_callback(payload)


@frappe.whitelist()
def get_foodpanda_webhook_urls():
	_require_permission()
	base_url = get_url().rstrip("/")
	method_base = f"{base_url}/api/method/aimatic.foodpanda_integration.api"
	return {
		"order_webhook_url": f"{method_base}.foodpanda_order_webhook",
		"assortment_webhook_url": f"{method_base}.foodpanda_catalog_webhook",
		"authorization": "Use the exact Webhook Secret value stored in Foodpanda Settings",
	}


@frappe.whitelist()
def sync_catalog_item(item_code, outlet):
	_require_permission()
	return catalog.sync_item(item_code, outlet)


@frappe.whitelist()
def apply_initial_foodpanda_pim_catalog(outlet, file_url):
	"""One-time, idempotent PIM-name and missing-price initialization."""
	_require_permission()
	return pim_catalog.apply_initial_pim_catalog(outlet, file_url)


@frappe.whitelist()
def retrieve_foodpanda_catalog(outlet):
	_require_permission()
	return catalog.get_remote_catalog(outlet)


@frappe.whitelist()
def retrieve_foodpanda_categories(outlet):
	_require_permission()
	return catalog.get_remote_categories(outlet)


@frappe.whitelist()
def request_foodpanda_catalog_export(outlet):
	_require_permission()
	return catalog.request_remote_export(outlet)


@frappe.whitelist()
def refresh_foodpanda_catalog_job(job_id):
	_require_permission()
	return catalog.refresh_remote_job(job_id)


@frappe.whitelist()
def start_catalog_bulk_export(outlet):
	_require_permission()
	return catalog.start_bulk_export(outlet)


@frappe.whitelist()
def get_catalog_bulk_export_status(job_id):
	_require_permission()
	return catalog.get_bulk_export_status(job_id)


@frappe.whitelist()
def update_foodpanda_order_status(sales_order, status):
	_require_permission()
	return orders.push_status_update(sales_order, status)


@frappe.whitelist()
def update_outlet_status(outlet, status, reason=None, closed_until=None):
	_require_permission()
	return outlet_module.push_outlet_status(outlet, status, reason=reason, closed_until=closed_until)


@frappe.whitelist()
def refresh_outlet_status(outlet):
	_require_permission()
	return outlet_module.pull_outlet_status(outlet)
