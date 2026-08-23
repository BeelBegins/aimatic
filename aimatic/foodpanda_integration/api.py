import json

import frappe
from frappe import _
from frappe.utils import get_url, now_datetime

from aimatic.foodpanda_integration import (
	catalog,
	catalog_jobs,
	orders,
	pim_catalog,
	webhooks,
)
from aimatic.foodpanda_integration import (
	outlet as outlet_module,
)
from aimatic.foodpanda_integration.client import FoodpandaAPIError

_ALLOWED_ROLES = {"System Manager", "Buying Price Control"}


def _require_permission():
	if not _ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(
			_("You need the Buying Price Control role to manage Foodpanda sync."),
			frappe.PermissionError,
		)


# Indirect / Pelican vendors often never send RECEIVED; READY_FOR_PICKUP is the
# first actionable webhook. Direct Partner Picking still starts with RECEIVED.
_ORDER_CREATE_STATUSES = frozenset({"RECEIVED", "READY_FOR_PICKUP", "DISPATCHED"})
_ORDER_TERMINAL_STATUSES = frozenset({"CANCELLED", "DELIVERED"})


def _normalize_foodpanda_order_status(payload):
	status = payload.get("status") or (payload.get("order") or {}).get("status") or "RECEIVED"
	status = str(status).upper().replace(" ", "_")
	if status == "CANCELED":
		return "CANCELLED"
	return status


def _foodpanda_order_items(payload):
	return payload.get("items") or (payload.get("order") or {}).get("items") or []


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

	if not isinstance(payload, dict):
		frappe.throw(_("Invalid JSON payload"))

	foodpanda_order_id = (
		payload.get("order_id")
		or payload.get("external_order_id")
		or (payload.get("order") or {}).get("order_id")
	)
	if not foodpanda_order_id:
		frappe.throw(_("Webhook payload is missing order_id"))

	remote_status = _normalize_foodpanda_order_status(payload)
	raw_text = (
		raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
	)
	existing = frappe.db.get_value(
		"Foodpanda Order Log",
		{"foodpanda_order_id": foodpanda_order_id},
		["name", "status", "sales_order"],
		as_dict=True,
	)
	if existing:
		local_status = {"CANCELLED": "Rejected", "DELIVERED": "Fulfilled"}.get(remote_status, existing.status)
		frappe.db.set_value(
			"Foodpanda Order Log",
			existing.name,
			{
				"status": local_status,
				"remote_status": remote_status,
				"raw_payload": raw_text,
			},
		)
		return {"status": local_status, "sales_order": existing.sales_order, "duplicate": True}

	# Always persist an auditable row first. Indirect (Pelican) vendors send
	# READY_FOR_PICKUP as the first status — never silently Ignore those.
	outlet = orders.resolve_outlet(payload)
	log = frappe.get_doc(
		{
			"doctype": "Foodpanda Order Log",
			"foodpanda_order_id": foodpanda_order_id,
			"order_code": payload.get("order_code") or (payload.get("order") or {}).get("order_code"),
			"outlet": outlet.name,
			"status": "Received",
			"remote_status": remote_status,
			"transport_type": payload.get("transport_type")
			or (payload.get("order") or {}).get("transport_type"),
			"raw_payload": raw_text,
			"received_at": now_datetime(),
		}
	)
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	items = _foodpanda_order_items(payload)
	should_create_so = remote_status in _ORDER_CREATE_STATUSES and bool(items)
	if remote_status in _ORDER_TERMINAL_STATUSES:
		should_create_so = False
	if not should_create_so:
		log.db_set(
			{
				"status": "Rejected" if remote_status == "CANCELLED" else "Received",
				"error": _(
					"Logged Foodpanda status {0} without creating a Sales Order "
					"(create on RECEIVED / READY_FOR_PICKUP / DISPATCHED when items are present)."
				).format(remote_status),
			}
		)
		return {
			"status": "Logged",
			"remote_status": remote_status,
			"order_log": log.name,
			"sales_order": None,
		}

	order_payload = dict(payload)
	if not order_payload.get("items") and items:
		order_payload["items"] = items

	sp = "foodpanda_order_webhook"
	frappe.db.savepoint(sp)
	try:
		sales_order = orders.make_order_from_webhook(order_payload, outlet)
		frappe.db.release_savepoint(sp)
	except Exception as error:
		frappe.db.rollback(save_point=sp)
		log.db_set({"status": "Failed", "error": str(error)})
		try:
			orders.reject_order(outlet, foodpanda_order_id, reason=str(error)[:200])
		except FoodpandaAPIError:
			pass  # already logged inside reject_order
		return {"status": "Failed", "error": str(error), "order_log": log.name}

	log.db_set({"status": "Accepted", "sales_order": sales_order.name})

	return {"status": "Accepted", "sales_order": sales_order.name, "order_log": log.name}


@frappe.whitelist(allow_guest=True)
def foodpanda_catalog_webhook():
	"""Assortment/catalog completion callback configured in Vendor Portal."""
	webhooks.verify_webhook_authorization()
	raw = frappe.request.get_data()
	payload = None
	try:
		payload = json.loads(raw) if raw else None
	except (TypeError, ValueError):
		payload = None
	if not isinstance(payload, dict):
		# Foodpanda may post JSON that Frappe already mapped into form_dict.
		form = dict(frappe.form_dict or {})
		form.pop("cmd", None)
		if form.get("job_id"):
			payload = form
	if not isinstance(payload, dict):
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


def _read_aimatic_doc_file(*parts):
	"""Load an English markdown doc shipped with the aimatic app (no secrets)."""
	import os

	base = frappe.get_app_path("aimatic")
	path = os.path.join(base, *parts)
	if not os.path.isfile(path):
		frappe.throw(_("Documentation file was not found: {0}").format("/".join(parts)))
	with open(path, encoding="utf-8") as handle:
		return handle.read()


@frappe.whitelist()
def get_foodpanda_settings_readme():
	"""English README shown on the Foodpanda Settings Desk form."""
	_require_permission()
	return {"markdown": _read_aimatic_doc_file("aimatic", "doctype", "foodpanda_settings", "README.md")}


@frappe.whitelist()
def get_foodpanda_sync_guide():
	"""English synchronized-outlet guide for Desk help dialogs."""
	_require_permission()
	# docs/ lives at apps/aimatic/docs (sibling of the Python package).
	import os

	path = os.path.join(frappe.get_app_path("aimatic"), "..", "docs", "foodpanda-synchronized-outlet.md")
	path = os.path.abspath(path)
	app_root = os.path.abspath(os.path.join(frappe.get_app_path("aimatic"), ".."))
	if not path.startswith(app_root + os.sep) or not os.path.isfile(path):
		frappe.throw(_("Synchronized outlet guide was not found."))
	with open(path, encoding="utf-8") as handle:
		return {"markdown": handle.read()}


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
def map_foodpanda_catalog_by_barcode(outlet, source="auto"):
	"""Match existing Foodpanda catalog products to Items by barcode."""
	_require_permission()
	return catalog.map_remote_catalog_by_barcode(outlet, source=source)


@frappe.whitelist()
def start_catalog_import(outlet):
	"""Request Partner POST /catalog/export for one outlet."""
	_require_permission()
	return catalog.start_catalog_import(outlet)


@frappe.whitelist()
def start_import_and_map(outlet):
	"""Background: export download then barcode mapping."""
	_require_permission()
	return catalog.start_import_and_map(outlet)


@frappe.whitelist()
def start_catalog_bulk_export(outlet):
	_require_permission()
	return catalog.start_bulk_export(outlet)


@frappe.whitelist()
def start_catalog_bulk_push(outlet, item_codes=None):
	"""Bulk PUT price/stock for mapped Foodpanda Products."""
	_require_permission()
	return catalog.start_bulk_push(outlet, item_codes=item_codes)


@frappe.whitelist()
def apply_catalog_sheet_updates(
	outlet,
	price_updates=None,
	active_updates=None,
	link_match_ready=1,
	seed_remote_prices=1,
	push=1,
	push_item_codes=None,
):
	"""Catalog Sheet: save prices/active, link Match Ready, push to Foodpanda."""
	_require_permission()
	from aimatic.foodpanda_integration import catalog_sheet

	return catalog_sheet.apply_catalog_sheet_updates(
		outlet,
		price_updates=price_updates,
		active_updates=active_updates,
		link_match_ready=link_match_ready,
		seed_remote_prices=seed_remote_prices,
		push=push,
		push_item_codes=push_item_codes,
	)


@frappe.whitelist()
def get_outlet_catalog_dashboard(outlet):
	"""User-facing catalog health summary for one Foodpanda Outlet."""
	_require_permission()
	if not outlet or not frappe.db.exists("Foodpanda Outlet", outlet):
		frappe.throw(_("Foodpanda Outlet not found"))

	doc = frappe.get_doc("Foodpanda Outlet", outlet)
	remote = int(doc.remote_sku_count or 0)
	mapped = frappe.db.count(
		"Foodpanda Product",
		{"outlet": outlet, "foodpanda_product_id": ("is", "set")},
	)
	failed = frappe.db.count("Foodpanda Product", {"outlet": outlet, "sync_status": "Failed"})
	pending = frappe.db.count("Foodpanda Product", {"outlet": outlet, "sync_status": "Pending"})
	synced = frappe.db.count("Foodpanda Product", {"outlet": outlet, "sync_status": "Synced"})
	gap = max(remote - mapped, 0) if remote else 0

	matching_files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Foodpanda Outlet",
			"attached_to_name": outlet,
			"file_name": ("like", "foodpanda-barcode-matching%"),
		},
		fields=["file_name", "file_url", "creation"],
		order_by="creation desc",
		limit=1,
	)
	export_jobs = frappe.get_all(
		"Foodpanda Catalog Job",
		filters={"outlet": outlet, "operation": "Export"},
		fields=["name", "status", "product_count", "export_file", "submitted_at", "completed_at"],
		order_by="submitted_at desc",
		limit=1,
	)
	latest_export = export_jobs[0] if export_jobs else None
	latest_report = matching_files[0] if matching_files else None

	# Recommend one primary action so the Desk UI stays simple.
	if not doc.catalog_sync_enabled:
		next_action = "enable"
		next_label = _("Enable catalog sync on the outlet")
		next_help = _("Turn on Catalog Sync Enabled, then come back here.")
	elif mapped <= 0:
		next_action = "refresh_links"
		next_label = _("Refresh product links from Foodpanda")
		next_help = _("Download Foodpanda's product list and match barcodes to ERPNext items.")
	elif failed > 0:
		next_action = "push"
		next_label = _("Update prices & stock on Foodpanda")
		next_help = _(
			"{0} products are ready. {1} failed last time — review Failed products after push."
		).format(mapped, failed)
	else:
		next_action = "push"
		next_label = _("Update prices & stock on Foodpanda")
		next_help = _("Sends current branch Foodpanda price and Bin stock for {0} linked products.").format(
			mapped
		)

	return {
		"outlet": outlet,
		"branch": doc.branch,
		"vendor_id": doc.vendor_id,
		"catalog_sync_enabled": int(doc.catalog_sync_enabled or 0),
		"status_cache": doc.status_cache,
		"remote_sku_count": remote,
		"mapped_sku_count": mapped,
		"unmapped_remote_count": gap,
		"failed_count": failed,
		"pending_count": pending,
		"synced_count": synced,
		"last_catalog_import_at": doc.last_catalog_import_at,
		"last_catalog_import_job": doc.last_catalog_import_job,
		"latest_matching_report": latest_report,
		"latest_export_job": latest_export,
		"next_action": next_action,
		"next_label": next_label,
		"next_help": next_help,
		"summary_line": _("{0} ready to update · {1} need attention · {2} not linked to ERPNext").format(
			mapped, failed, gap
		),
	}


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
