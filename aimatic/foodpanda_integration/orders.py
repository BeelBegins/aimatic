import hashlib
import hmac

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from aimatic.branch_management.utils import get_branch_defaults
from aimatic.foodpanda_integration import client
from aimatic.foodpanda_integration.client import FoodpandaAPIError
from aimatic.shelf_pricing.utils import get_or_create_branch_foodpanda_price_list

# Path confirmed against developer.foodpanda.com/api-specifications (Order
# section): PUT is used for both accept and reject/cancel, keyed by a
# `status` field. The exact enum strings (ACCEPTED/CANCELLED below) and the
# inbound webhook's signature header/scheme are NOT confirmed against a real
# payload sample - there are no Foodpanda credentials on file yet. Both are
# isolated to single named constants specifically so they're a one-line fix
# once a real webhook delivery or sandbox response is available.
_ORDER_PATH = "/v2/chains/{chain_id}/orders/{order_id}"
_SIGNATURE_HEADER = "X-Foodpanda-Signature"
_STATUS_ACCEPTED = "ACCEPTED"
_STATUS_REJECTED = "CANCELLED"
_FOODPANDA_CUSTOMER = "Foodpanda"


def verify_webhook_signature(raw_body, signature_header):
	settings = frappe.get_single("Foodpanda Settings")
	secret = settings.get_password("webhook_secret", raise_exception=False)
	if not secret:
		frappe.throw(_("Foodpanda webhook secret is not configured"), frappe.PermissionError)
	if not signature_header:
		frappe.throw(_("Missing webhook signature"), frappe.PermissionError)

	expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
	if not hmac.compare_digest(expected, signature_header):
		frappe.throw(_("Webhook signature verification failed"), frappe.PermissionError)


def resolve_outlet(payload):
	vendor_id = payload.get("vendor_id") or payload.get("vendor_code")
	if not vendor_id:
		frappe.throw(_("Webhook payload is missing a vendor id"))

	outlet_name = frappe.db.get_value(
		"Foodpanda Outlet", {"vendor_id": vendor_id, "order_ingestion_enabled": 1}, "name"
	)
	if not outlet_name:
		frappe.throw(_("No Foodpanda Outlet is enabled for vendor {0}").format(vendor_id))
	return frappe.get_doc("Foodpanda Outlet", outlet_name)


def _foodpanda_customer():
	"""One shared Customer for every Foodpanda order, same idea as ERPNext's
	usual walk-in customer - Foodpanda orders aren't tied to an individual
	ERPNext-known customer record the way aimatic.shopping orders are (those
	come from a signed-in Website User/Portal User)."""
	if not frappe.db.exists("Customer", _FOODPANDA_CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": _FOODPANDA_CUSTOMER,
				"customer_type": "Company",
				"customer_group": frappe.db.get_single_value("Selling Settings", "customer_group")
				or "All Customer Groups",
				"territory": frappe.db.get_single_value("Selling Settings", "territory") or "All Territories",
			}
		).insert(ignore_permissions=True)
	return _FOODPANDA_CUSTOMER


def make_order_from_webhook(payload, outlet):
	"""Builds and inserts a draft Sales Order from a Foodpanda order webhook
	payload - modeled on aimatic.shopping.api._make_order, including staying
	at docstatus 0 (a draft for staff to review/submit) rather than
	auto-submitting. Raises on any problem (unknown item, insufficient
	stock); the caller is responsible for rolling back and rejecting the
	order with Foodpanda when this raises.
	"""
	branch_defaults = get_branch_defaults(outlet.branch)
	warehouse = branch_defaults.get("finished_goods_warehouse")
	if not warehouse:
		frappe.throw(_("Branch {0} has no Finished Goods Warehouse").format(outlet.branch))

	company = frappe.db.get_value("Branch", outlet.branch, "company")
	customer = _foodpanda_customer()
	price_list = get_or_create_branch_foodpanda_price_list(outlet.branch)

	items = payload.get("items") or []
	if not items:
		frappe.throw(_("Foodpanda order has no items"))

	doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer,
			"company": company,
			"branch": outlet.branch,
			"set_warehouse": warehouse,
			"selling_price_list": price_list,
			"order_type": "Sales",
			"delivery_date": nowdate(),
			"remarks": f"Foodpanda order {payload.get('order_id')}",
		}
	)
	doc.flags.ignore_permissions = True

	for row in items:
		item_code = row.get("sku") or row.get("product_id")
		qty = flt(row.get("quantity") or row.get("qty") or 1)
		if not item_code or not frappe.db.exists("Item", item_code):
			frappe.throw(_("Unknown item {0} in Foodpanda order").format(item_code))

		bin_row = (
			frappe.db.get_value(
				"Bin", {"item_code": item_code, "warehouse": warehouse}, ["actual_qty", "reserved_qty"], as_dict=True
			)
			or {}
		)
		available = flt(bin_row.get("actual_qty")) - flt(bin_row.get("reserved_qty"))
		if available + 0.0001 < qty:
			frappe.throw(_("Insufficient stock for {0}").format(item_code))

		doc.append("items", {"item_code": item_code, "qty": qty, "warehouse": warehouse})

	doc.run_method("set_missing_values")
	doc.run_method("calculate_taxes_and_totals")
	doc.insert(ignore_permissions=True)
	return doc


def accept_order(outlet, foodpanda_order_id):
	settings = client.get_settings()
	try:
		client.request(
			"PUT",
			_ORDER_PATH.format(chain_id=settings.chain_id, order_id=foodpanda_order_id),
			settings=settings,
			json={"status": _STATUS_ACCEPTED},
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(
			f"Foodpanda order accept failed: {foodpanda_order_id}", f"Outlet: {outlet.name}", error
		)
		raise


def reject_order(outlet, foodpanda_order_id, reason=None):
	settings = client.get_settings()
	payload = {"status": _STATUS_REJECTED}
	if reason:
		payload["cancellation"] = {"reason": reason}
	try:
		client.request(
			"PUT",
			_ORDER_PATH.format(chain_id=settings.chain_id, order_id=foodpanda_order_id),
			settings=settings,
			json=payload,
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(
			f"Foodpanda order reject failed: {foodpanda_order_id}", f"Outlet: {outlet.name}", error
		)
		raise


def push_status_update(sales_order, status):
	"""Extension point for preparing/ready/picked-up sync-back, intentionally
	not wired to anything yet - there is no existing kitchen/fulfillment
	status-transition hook in this app to attach it to (confirmed while
	researching this integration), so guessing at that lifecycle here would
	be speculative. Call this explicitly once such a trigger exists."""
	raise NotImplementedError(
		"push_status_update has no caller yet - wire it to a real fulfillment status transition first"
	)
