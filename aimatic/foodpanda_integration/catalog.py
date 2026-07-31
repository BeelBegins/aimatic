import hashlib
import json
import uuid

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from aimatic.branch_management.utils import get_branch_defaults
from aimatic.foodpanda_integration import client
from aimatic.foodpanda_integration.client import FoodpandaAPIError
from aimatic.shelf_pricing.utils import get_or_create_branch_foodpanda_price_list

# Confirmed against developer.foodpanda.com/en/documentation/catalog-api-use-cases
# (2026-07-31) - this supersedes the earlier, less specific api-specifications
# page. Still not exercised against a live/sandbox call (no credentials on
# file); re-verify before first use.
#
# - POST /v2/chains/{chain_id}/vendors/{vendor_id}/catalog          add products (async, 202 + job_id)
# - PUT  /v2/chains/{chain_id}/vendors/{vendor_id}/catalog          update products (async, 202 + job_id)
# - GET  /v2/chains/{chain_id}/catalog/jobs/{job_id}                job status: QUEUED/IN_PROGRESS/COMPLETED/FAILED
# - GET  /v2/chains/{chain_id}/vendors/{vendor_id}/categories       list categories (not called yet - see below)
# - POST /v2/chains/{chain_id}/vendors/{vendor_id}/catalog/export   full catalog export
#
# Confirmed update-payload fields (docs example: {"products": [{"sku": "LS-33",
# "active": false, "price": 10}]}): sku, active, price, barcode, quantity,
# maximum_sales_quantity. "quantity acts as a reference in comparison with the
# already configured sales buffer" - i.e. push the real stock number, not just
# a boolean, and let Foodpanda's own buffer decide availability alongside our
# `active` flag. `maximum_sales_quantity` has no ERPNext-side source and is
# not sent. The docs also mention a completion webhook for these jobs, not
# implemented here - this module still does a single synchronous status check
# instead (see _job_succeeded).
#
# The add/create payload's exact field set is NOT shown in the fetched docs
# (only the update example is) - name/description/category_id below are kept
# for creation only, on the assumption a brand-new listing needs them, but
# this is unconfirmed. category_id also depends on Foodpanda Category Map,
# which is unconfirmed against the categories endpoint above (not yet
# queried).
_ADD_PRODUCTS_PATH = "/v2/chains/{chain_id}/vendors/{vendor_id}/catalog"
_UPDATE_PRODUCTS_PATH = "/v2/chains/{chain_id}/vendors/{vendor_id}/catalog"
_JOB_STATUS_PATH = "/v2/chains/{chain_id}/catalog/jobs/{job_id}"

_JOB_CACHE_PREFIX = "aimatic:foodpanda:bulkexport:"
_JOB_CACHE_TTL = 24 * 60 * 60


def _get_item_price(item_code, branch):
	price_list = get_or_create_branch_foodpanda_price_list(branch)
	rate = frappe.db.get_value(
		"Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}, "price_list_rate"
	)
	return flt(rate, 2)


def _get_stock(item_code, branch):
	"""Returns (quantity, active) for one branch's Bin - quantity is the real
	sellable stock number Foodpanda's `quantity` field expects, active is
	just quantity > 0. A single Bin lookup backs both."""
	warehouse = get_branch_defaults(branch).get("finished_goods_warehouse")
	if not warehouse:
		return 0, False
	bin_row = frappe.db.get_value(
		"Bin", {"item_code": item_code, "warehouse": warehouse}, ["actual_qty", "reserved_qty"], as_dict=True
	) or {}
	quantity = max(flt(bin_row.get("actual_qty")) - flt(bin_row.get("reserved_qty")), 0)
	return quantity, quantity > 0


def _get_barcode(item_code):
	return frappe.db.get_value("Item Barcode", {"parent": item_code}, "barcode")


def build_update_payload(item_code, outlet):
	"""Steady-state payload for an existing Foodpanda product - limited to
	the fields the docs' update example actually shows."""
	item = frappe.db.get_value(
		"Item", item_code, ["disabled", "is_sales_item"], as_dict=True
	)
	if not item:
		frappe.throw(_("Item {0} does not exist").format(item_code))

	price = _get_item_price(item_code, outlet.branch)
	quantity, in_stock = _get_stock(item_code, outlet.branch)
	active = bool(item.is_sales_item) and not item.disabled and in_stock

	payload = {"sku": item_code, "active": active, "price": price, "quantity": quantity}
	barcode = _get_barcode(item_code)
	if barcode:
		payload["barcode"] = barcode
	return payload


def build_create_payload(item_code, outlet):
	"""Payload for a brand-new Foodpanda product. Field set beyond the
	update-payload fields (name/description/category_id) is unconfirmed -
	see the module docstring."""
	item = frappe.db.get_value(
		"Item", item_code, ["item_name", "description", "item_group", "disabled", "is_sales_item"], as_dict=True
	)
	if not item:
		frappe.throw(_("Item {0} does not exist").format(item_code))

	category_id = frappe.db.get_value("Foodpanda Category Map", item.item_group, "foodpanda_category_id")
	payload = build_update_payload(item_code, outlet)
	payload["name"] = item.item_name
	payload["description"] = item.description or ""
	payload["category_id"] = category_id
	return payload


def hash_payload(payload):
	return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def get_or_create_foodpanda_product(item_code, outlet_name):
	name = frappe.db.get_value("Foodpanda Product", {"item_code": item_code, "outlet": outlet_name}, "name")
	if name:
		return frappe.get_doc("Foodpanda Product", name)

	doc = frappe.get_doc(
		{
			"doctype": "Foodpanda Product",
			"item_code": item_code,
			"outlet": outlet_name,
			"sync_status": "Pending",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _submit_catalog_job(settings, method, chain_id, vendor_id, payload):
	response = client.request(
		method,
		_ADD_PRODUCTS_PATH.format(chain_id=chain_id, vendor_id=vendor_id),
		settings=settings,
		json={"products": [payload]},
	)
	return response.json().get("job_id")


def _job_succeeded(settings, chain_id, job_id):
	"""Single status check, not a poll loop - a brand-new job may still show
	QUEUED/IN_PROGRESS here. That's left as sync_status=Pending (not a
	failure) for a later manual retry/bulk export pass to pick up, rather
	than blocking this request on an unbounded wait. Foodpanda's docs mention
	a completion webhook for these jobs; that's not implemented here yet."""
	if not job_id:
		return None
	response = client.request(
		"GET", _JOB_STATUS_PATH.format(chain_id=chain_id, job_id=job_id), settings=settings
	)
	status = (response.json().get("status") or "").upper()
	if status == "COMPLETED":
		return True
	if status == "FAILED":
		return False
	return None  # QUEUED or IN_PROGRESS


def sync_item(item_code, outlet_name):
	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	if not outlet.catalog_sync_enabled:
		return {"status": "Skipped", "reason": "Catalog sync disabled for this outlet"}

	product = get_or_create_foodpanda_product(item_code, outlet_name)
	is_new_product = not product.foodpanda_product_id
	payload = build_create_payload(item_code, outlet) if is_new_product else build_update_payload(item_code, outlet)
	content_hash = hash_payload(payload)

	if not is_new_product and product.sync_status == "Synced" and product.content_hash == content_hash:
		return {"status": "Synced", "skipped": True}

	settings = client.get_settings()
	try:
		method = "POST" if is_new_product else "PUT"
		job_id = _submit_catalog_job(settings, method, settings.chain_id, outlet.vendor_id, payload)
		succeeded = _job_succeeded(settings, settings.chain_id, job_id)

		if succeeded:
			values = {"sync_status": "Synced", "content_hash": content_hash, "last_synced": now_datetime(), "last_error": ""}
			if is_new_product:
				values["foodpanda_product_id"] = item_code
			product.db_set(values)
		elif succeeded is False:
			product.db_set({"sync_status": "Failed", "last_error": "Foodpanda rejected the catalog job"})
		else:
			product.db_set({"sync_status": "Pending", "last_error": ""})
	except FoodpandaAPIError as error:
		client.log_api_failure(
			f"Foodpanda catalog sync failed: {item_code}", f"Outlet: {outlet_name}", error
		)
		product.db_set({"sync_status": "Failed", "last_error": str(error)})
		return {"status": "Failed", "error": str(error)}

	return {"status": product.sync_status}


def sync_availability(item_code, branch):
	outlet_name = frappe.db.get_value(
		"Foodpanda Outlet", {"branch": branch, "catalog_sync_enabled": 1}, "name"
	)
	if not outlet_name:
		return

	product = frappe.db.get_value(
		"Foodpanda Product",
		{"item_code": item_code, "outlet": outlet_name},
		["name", "foodpanda_product_id"],
		as_dict=True,
	)
	# Nothing pushed yet for this item at this outlet - a full sync_item call
	# (bulk export or the next catalog change) carries stock/availability
	# with it, no need to push a partial update ahead of the item existing.
	if not product or not product.foodpanda_product_id:
		return

	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	settings = client.get_settings()

	try:
		payload = build_update_payload(item_code, outlet)
		job_id = _submit_catalog_job(settings, "PUT", settings.chain_id, outlet.vendor_id, payload)
		succeeded = _job_succeeded(settings, settings.chain_id, job_id)
		status = "Synced" if succeeded else ("Failed" if succeeded is False else "Pending")
		frappe.db.set_value(
			"Foodpanda Product",
			product.name,
			{"sync_status": status, "last_synced": now_datetime(), "last_error": "" if succeeded else "Foodpanda job did not complete"},
		)
	except FoodpandaAPIError as error:
		client.log_api_failure(
			f"Foodpanda availability sync failed: {item_code}", f"Outlet: {outlet_name}", error
		)
		frappe.db.set_value("Foodpanda Product", product.name, {"sync_status": "Failed", "last_error": str(error)})


def _job_cache_key(job_id):
	return f"{_JOB_CACHE_PREFIX}{job_id}"


def _set_job_status(job_id, values):
	current = _get_job_status(job_id) or {}
	current.update(values)
	frappe.cache.set_value(_job_cache_key(job_id), json.dumps(current), expires_in_sec=_JOB_CACHE_TTL)


def _get_job_status(job_id):
	raw = frappe.cache.get_value(_job_cache_key(job_id))
	if not raw:
		return None
	if isinstance(raw, bytes):
		raw = raw.decode()
	return json.loads(raw)


def start_bulk_export(outlet_name):
	outlet = frappe.get_doc("Foodpanda Outlet", outlet_name)
	if not outlet.catalog_sync_enabled:
		frappe.throw(_("Catalog sync is not enabled for this outlet"))

	job_id = uuid.uuid4().hex
	_set_job_status(
		job_id,
		{
			"job_id": job_id,
			"outlet": outlet_name,
			"owner": frappe.session.user,
			"status": "queued",
			"synced": 0,
			"failed": 0,
			"total": 0,
		},
	)
	frappe.enqueue(
		"aimatic.foodpanda_integration.catalog.run_bulk_export",
		queue="long",
		job_id=job_id,
		outlet_name=outlet_name,
		enqueue_after_commit=True,
		job_name=f"Foodpanda bulk export {outlet_name}",
	)
	return _get_job_status(job_id)


def get_bulk_export_status(job_id):
	status = _get_job_status(job_id)
	if not status:
		frappe.throw(_("Export job expired or was not found"))
	if status.get("owner") != frappe.session.user:
		frappe.throw(_("This export job is not available to you"), frappe.PermissionError)
	return status


def run_bulk_export(job_id, outlet_name):
	status = _get_job_status(job_id)
	if not status:
		return

	item_codes = frappe.get_all("Item", filters={"disabled": 0, "is_sales_item": 1}, pluck="name")
	_set_job_status(job_id, {"status": "running", "total": len(item_codes)})

	synced, failed = 0, 0
	for item_code in item_codes:
		try:
			result = sync_item(item_code, outlet_name)
			if result.get("status") == "Failed":
				failed += 1
			else:
				synced += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Foodpanda bulk export failed for {item_code}")
			failed += 1
		_set_job_status(job_id, {"synced": synced, "failed": failed})

	_set_job_status(job_id, {"status": "done"})
