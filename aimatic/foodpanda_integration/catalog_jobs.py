import json

import frappe
from frappe import _
from frappe.utils import now_datetime

_SUCCESS_STATUSES = {"COMPLETED", "SUCCESS", "SUCCEEDED"}
_FAILURE_STATUSES = {"FAILED", "FAILURE", "ERROR"}


def normalize_status(payload):
	remote_status = str(payload.get("job_status") or payload.get("status") or "").upper()
	if remote_status in _SUCCESS_STATUSES:
		return "Completed"
	if remote_status in _FAILURE_STATUSES:
		return "Failed"
	return "Pending"


def _feedback_error(payload):
	result = payload.get("result") or {}
	feedback = result.get("item_level_feedback") or payload.get("item_level_feedback") or []
	if feedback:
		return json.dumps(feedback, ensure_ascii=False, default=str)[:4000]
	return str(payload.get("message") or payload.get("error") or "Foodpanda catalog job failed")[:4000]


def _feedback_for_sku(payload, sku):
	result = payload.get("result") or {}
	feedback = result.get("item_level_feedback") or payload.get("item_level_feedback") or []
	matches = []
	for row in feedback:
		if not isinstance(row, dict):
			continue
		row_sku = row.get("sku") or row.get("product_sku") or row.get("item_sku")
		if str(row_sku or "") == str(sku):
			matches.append(row)
	return json.dumps(matches, ensure_ascii=False, default=str)[:4000] if matches else ""


def process_callback(payload):
	if not isinstance(payload, dict):
		frappe.throw(_("Foodpanda catalog callback must be a JSON object"))

	job_id = payload.get("job_id")
	if not job_id:
		frappe.throw(_("Foodpanda catalog callback is missing job_id"))

	status = normalize_status(payload)
	download_url = payload.get("download_url")
	job_name = frappe.db.get_value("Foodpanda Catalog Job", {"job_id": job_id}, "name")
	values = {
		"status": status,
		"raw_response": json.dumps(payload, ensure_ascii=False, default=str),
		"download_url": download_url,
		"error": _feedback_error(payload) if status == "Failed" else "",
	}
	if status in {"Completed", "Failed"}:
		values["completed_at"] = now_datetime()

	if job_name:
		frappe.db.set_value("Foodpanda Catalog Job", job_name, values)
	else:
		vendor_id = payload.get("platform_vendor_id") or payload.get("vendor_id")
		outlet_name = frappe.db.get_value("Foodpanda Outlet", {"vendor_id": vendor_id}, "name") if vendor_id else None
		frappe.get_doc(
			{
				"doctype": "Foodpanda Catalog Job",
				"job_id": job_id,
				"operation": "Callback",
				"outlet": outlet_name,
				"vendor_id": vendor_id,
				"submitted_at": now_datetime(),
				**values,
			}
		).insert(ignore_permissions=True)

	products = frappe.get_all(
		"Foodpanda Product",
		filters={"last_job_id": job_id},
		fields=["name", "item_code", "foodpanda_product_id", "pending_content_hash"],
	)
	for product in products:
		product_error = _feedback_for_sku(payload, product.item_code)
		if status == "Completed" and not product_error:
			product_values = {
				"sync_status": "Synced",
				"content_hash": product.pending_content_hash,
				"pending_content_hash": "",
				"last_synced": now_datetime(),
				"last_error": "",
			}
			if not product.foodpanda_product_id:
				product_values["foodpanda_product_id"] = product.item_code
		else:
			product_values = {
				"sync_status": "Failed" if status == "Failed" or product_error else "Pending",
				"last_error": product_error or (values["error"] if status == "Failed" else ""),
			}
		frappe.db.set_value("Foodpanda Product", product.name, product_values)

	return {"job_id": job_id, "status": status, "products_updated": len(products)}
