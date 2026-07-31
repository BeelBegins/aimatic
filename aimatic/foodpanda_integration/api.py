import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from aimatic.foodpanda_integration import catalog, orders, outlet as outlet_module
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
	Verifies the signature, dedupes by foodpanda_order_id, and turns a new
	order into a draft Sales Order - see aimatic.foodpanda_integration.orders
	for the per-step logic and why the log row is committed before the
	Sales-Order-creation savepoint is opened.
	"""
	raw = frappe.request.get_data()
	signature = frappe.get_request_header(orders._SIGNATURE_HEADER)
	orders.verify_webhook_signature(raw, signature)

	try:
		payload = json.loads(raw)
	except (TypeError, ValueError):
		frappe.throw(_("Invalid JSON payload"))

	foodpanda_order_id = payload.get("order_id")
	if not foodpanda_order_id:
		frappe.throw(_("Webhook payload is missing order_id"))

	existing = frappe.db.get_value(
		"Foodpanda Order Log",
		{"foodpanda_order_id": foodpanda_order_id},
		["status", "sales_order"],
		as_dict=True,
	)
	if existing:
		return {"status": existing.status, "sales_order": existing.sales_order, "duplicate": True}

	outlet = orders.resolve_outlet(payload)

	log = frappe.get_doc(
		{
			"doctype": "Foodpanda Order Log",
			"foodpanda_order_id": foodpanda_order_id,
			"outlet": outlet.name,
			"status": "Received",
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
	try:
		orders.accept_order(outlet, foodpanda_order_id)
	except FoodpandaAPIError as error:
		# The Sales Order exists but Foodpanda wasn't told - flag for manual
		# follow-up rather than deleting an order that's already in ERPNext.
		log.db_set({"error": f"Sales Order {sales_order.name} created but the accept call failed: {error}"})

	return {"status": "Accepted", "sales_order": sales_order.name}


@frappe.whitelist()
def sync_catalog_item(item_code, outlet):
	_require_permission()
	return catalog.sync_item(item_code, outlet)


@frappe.whitelist()
def start_catalog_bulk_export(outlet):
	_require_permission()
	return catalog.start_bulk_export(outlet)


@frappe.whitelist()
def get_catalog_bulk_export_status(job_id):
	_require_permission()
	return catalog.get_bulk_export_status(job_id)


@frappe.whitelist()
def update_outlet_status(outlet, status, reason=None, closed_until=None):
	_require_permission()
	return outlet_module.push_outlet_status(outlet, status, reason=reason, closed_until=closed_until)


@frappe.whitelist()
def refresh_outlet_status(outlet):
	_require_permission()
	return outlet_module.pull_outlet_status(outlet)
