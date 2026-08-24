import frappe
from frappe import _
from frappe.utils import flt, nowdate

from aimatic.branch_management.utils import get_branch_defaults
from aimatic.foodpanda_integration import client
from aimatic.foodpanda_integration.catalog import _barcode_variants
from aimatic.foodpanda_integration.client import FoodpandaAPIError
from aimatic.shelf_pricing.utils import get_or_create_branch_foodpanda_price_list

_ORDER_PATH = "/v2/chains/{chain_id}/orders/{order_id}"
_STATUS_REJECTED = "CANCELLED"
_CANCELLATION_REASON_ITEM_UNAVAILABLE = "ITEM_UNAVAILABLE"
_ALLOWED_OUTBOUND_STATUSES = {"READY_FOR_PICKUP", "DISPATCHED"}
_FOODPANDA_CUSTOMER = "Foodpanda"


def resolve_outlet(payload):
	client_payload = payload.get("client") or {}
	vendor_id = (
		client_payload.get("external_partner_config_id")
		or client_payload.get("store_id")
		or client_payload.get("id")
		or payload.get("vendor_id")
		or payload.get("vendor_code")
	)
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


def _resolve_order_item_code(sku, outlet_name):
	"""Map a Foodpanda order SKU to an Item via barcode mapping, never assume Item Code."""
	if not sku:
		return None
	sku = str(sku).strip()

	mapped = frappe.db.get_value(
		"Foodpanda Product",
		{"outlet": outlet_name, "foodpanda_product_id": sku},
		"item_code",
	)
	if mapped:
		return mapped

	candidates = []
	for variant in _barcode_variants(sku):
		candidates.extend(frappe.get_all("Item Barcode", filters={"barcode": variant}, pluck="parent"))
		if frappe.db.exists("Item", variant):
			candidates.append(variant)
	unique = list({code for code in candidates if code})
	return unique[0] if len(unique) == 1 else None


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
		remote_sku = row.get("sku") or row.get("product_id")
		pricing = row.get("pricing") or {}
		qty = flt(pricing.get("quantity") or row.get("quantity") or row.get("qty") or 1)
		unit_price = pricing.get("unit_price")
		item_code = _resolve_order_item_code(remote_sku, outlet.name)
		if not item_code:
			frappe.throw(_("Unknown item {0} in Foodpanda order").format(remote_sku))

		bin_row = (
			frappe.db.get_value(
				"Bin",
				{"item_code": item_code, "warehouse": warehouse},
				["actual_qty", "reserved_qty"],
				as_dict=True,
			)
			or {}
		)
		available = flt(bin_row.get("actual_qty")) - flt(bin_row.get("reserved_qty"))
		if available + 0.0001 < qty:
			frappe.throw(_("Insufficient stock for {0}").format(item_code))

		item_values = {"item_code": item_code, "qty": qty, "warehouse": warehouse}
		if unit_price is not None:
			item_values.update({"rate": flt(unit_price), "price_list_rate": flt(unit_price)})
		doc.append("items", item_values)

	doc.run_method("set_missing_values")
	doc.run_method("calculate_taxes_and_totals")
	doc.insert(ignore_permissions=True)
	return doc


def reject_order(outlet, foodpanda_order_id, reason=None):
	settings = client.get_settings()
	payload = {"status": _STATUS_REJECTED, "cancellation": {"reason": _CANCELLATION_REASON_ITEM_UNAVAILABLE}}
	try:
		client.request(
			"PUT",
			_ORDER_PATH.format(
				chain_id=client.get_chain_id(outlet, settings=settings), order_id=foodpanda_order_id
			),
			settings=settings,
			json=payload,
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(
			f"Foodpanda order reject failed: {foodpanda_order_id}", f"Outlet: {outlet.name}", error
		)
		raise


def push_status_update(sales_order, status):
	remote_status = str(status or "").upper().replace(" ", "_")
	if remote_status not in _ALLOWED_OUTBOUND_STATUSES:
		frappe.throw(_("Foodpanda order status must be READY_FOR_PICKUP or DISPATCHED"))

	order_log = frappe.db.get_value(
		"Foodpanda Order Log",
		{"sales_order": sales_order},
		["name", "foodpanda_order_id", "outlet"],
		as_dict=True,
	)
	if not order_log:
		frappe.throw(_("Sales Order {0} is not linked to a Foodpanda order").format(sales_order))

	settings = client.get_settings()
	outlet = frappe.get_doc("Foodpanda Outlet", order_log.outlet) if order_log.outlet else None
	try:
		client.request(
			"PUT",
			_ORDER_PATH.format(
				chain_id=client.get_chain_id(outlet, settings=settings),
				order_id=order_log.foodpanda_order_id,
			),
			settings=settings,
			json={"status": remote_status},
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(
			f"Foodpanda order status failed: {order_log.foodpanda_order_id}",
			f"Sales Order: {sales_order}; status: {remote_status}",
			error,
		)
		raise

	frappe.db.set_value("Foodpanda Order Log", order_log.name, "remote_status", remote_status)
	return {"foodpanda_order_id": order_log.foodpanda_order_id, "status": remote_status}
