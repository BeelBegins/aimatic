import json
import traceback

import frappe
import requests
from frappe import _
from frappe.utils import now

from aimatic.fbr_pos.payload_builder import build_pos_payload, get_invoice_branch
from aimatic.fbr_pos.settings import resolve_fbr_settings


def get_settings_from_doc(doc):
	branch = get_invoice_branch(doc)
	return resolve_fbr_settings(doc, doc.company, branch)


def normalize_fbr_response(response, payload):
	try:
		data = response.json()
	except Exception:
		data = {
			"raw_response": response.text,
		}

	data["HTTPStatusCode"] = response.status_code

	success = False

	status_code = str(data.get("StatusCode") or data.get("Code") or "")

	invoice_number = data.get("InvoiceNumber") or data.get("FBRInvoiceNumber") or data.get("invoiceNumber")

	invoice_number = data.get("InvoiceNumber") or data.get("FBRInvoiceNumber") or data.get("invoiceNumber")

	if invoice_number and str(invoice_number).strip().lower() in [
		"not available",
		"n/a",
		"na",
		"none",
		"null",
	]:
		invoice_number = None

	# Your old integration treated Code 100 as success.
	if status_code in ["100", "00"]:
		success = True

	if response.status_code in [200, 201] and invoice_number and status_code not in ["102"]:
		success = True

	return success, data, invoice_number


def submit_payload_to_fbr(payload, settings):
	headers = {
		"Content-Type": "application/json",
	}

	if settings.get("security_token"):
		headers["Authorization"] = f"Bearer {settings['security_token']}"

	response = requests.post(
		settings["api_url"],
		headers=headers,
		data=json.dumps(payload, ensure_ascii=False),
		timeout=int(settings.get("request_timeout") or 30),
		verify=bool(settings.get("verify_ssl")),
	)

	return normalize_fbr_response(response, payload)


def log_fbr_submission_failure(doc, payload, response_data):
	"""
	block_submit_on_failure rolls back the whole insert+submit transaction
	(submit_online_sale runs it inside a named savepoint specifically so a
	failure here can roll back cleanly), so
	doc.custom_fbr_request_payload/custom_fbr_response_payload never reach
	the database. defer_insert=True writes to redis instead of the current
	SQL transaction, so the log survives that rollback - a plain
	frappe.db.commit() here would instead consume the caller's savepoint out
	from under it and permanently leave the just-inserted draft invoice
	behind, so don't "simplify" this back to a commit.
	"""

	request_json = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
	response_json = json.dumps(response_data, indent=2, ensure_ascii=False, default=str)

	frappe.log_error(
		title=f"FBR POS submission failed: {doc.name}",
		message=(
			f"Company: {doc.company}\n"
			f"POS Invoice: {doc.name}\n\n"
			f"Request payload:\n{request_json}\n\n"
			f"Response:\n{response_json}"
		),
		defer_insert=True,
	)


def apply_fbr_result_to_doc(
	doc,
	payload,
	success,
	response_data,
	invoice_number=None,
):
	request_json = json.dumps(
		payload,
		indent=2,
		ensure_ascii=False,
		default=str,
	)

	response_json = json.dumps(
		response_data,
		indent=2,
		ensure_ascii=False,
		default=str,
	)

	if doc.meta.has_field("custom_fbr_request_payload"):
		doc.custom_fbr_request_payload = request_json

	if doc.meta.has_field("custom_fbr_response_payload"):
		doc.custom_fbr_response_payload = response_json

	if doc.meta.has_field("custom_fbr_http_status"):
		doc.custom_fbr_http_status = response_data.get("HTTPStatusCode")

	if doc.meta.has_field("custom_fbr_submission_date"):
		doc.custom_fbr_submission_date = now()

	if doc.meta.has_field("custom_fbr_usin"):
		doc.custom_fbr_usin = payload.get("USIN")

	if success:
		if doc.meta.has_field("custom_fbr_status"):
			doc.custom_fbr_status = "Accepted"

		if doc.meta.has_field("custom_fbr_invoice_number"):
			doc.custom_fbr_invoice_number = invoice_number or ""

		if doc.meta.has_field("custom_fbr_error"):
			doc.custom_fbr_error = ""

	else:
		if doc.meta.has_field("custom_fbr_status"):
			doc.custom_fbr_status = "Failed"

		if doc.meta.has_field("custom_fbr_error"):
			doc.custom_fbr_error = (
				response_data.get("StatusMessage")
				or response_data.get("Response")
				or response_data.get("Errors")
				or response_json
			)


def submit_pos_invoice_to_fbr(doc):
	settings = get_settings_from_doc(doc)

	if settings is None:
		return {
			"success": False,
			"skipped": True,
			"payload": None,
			"response": None,
		}

	payload = build_pos_payload(doc)

	submission_mode = settings.get("submission_mode")

	if submission_mode in ["Disabled", "Preview Only"]:
		apply_fbr_result_to_doc(
			doc=doc,
			payload=payload,
			success=False,
			response_data={
				"StatusCode": "PREVIEW_ONLY",
				"StatusMessage": ("FBR payload generated only. Submission mode is Preview Only or Disabled."),
				"HTTPStatusCode": 0,
			},
			invoice_number=None,
		)

		return {
			"success": False,
			"payload": payload,
			"response": {
				"StatusCode": "PREVIEW_ONLY",
			},
		}

	if doc.meta.has_field("custom_fbr_status"):
		doc.custom_fbr_status = "Sending"

	try:
		success, response_data, invoice_number = submit_payload_to_fbr(
			payload,
			settings,
		)

		apply_fbr_result_to_doc(
			doc=doc,
			payload=payload,
			success=success,
			response_data=response_data,
			invoice_number=invoice_number,
		)

		if not success and settings.get("block_submit_on_failure"):
			log_fbr_submission_failure(doc, payload, response_data)

			frappe.throw(
				_("FBR submission failed. Invoice was not submitted. Response: {0}").format(
					frappe.as_json(response_data)
				)
			)

		return {
			"success": success,
			"payload": payload,
			"response": response_data,
			"invoice_number": invoice_number,
		}

	except Exception as e:
		error_data = {
			"StatusCode": "EXCEPTION",
			"StatusMessage": str(e),
			"Traceback": traceback.format_exc(),
			"HTTPStatusCode": 0,
		}

		apply_fbr_result_to_doc(
			doc=doc,
			payload=payload,
			success=False,
			response_data=error_data,
			invoice_number=None,
		)

		if settings.get("block_submit_on_failure"):
			log_fbr_submission_failure(doc, payload, error_data)

			frappe.throw(_("FBR submission error: {0}").format(str(e)))

		return {
			"success": False,
			"payload": payload,
			"response": error_data,
		}


@frappe.whitelist()
def preview_payload(pos_invoice):
	doc = frappe.get_doc("POS Invoice", pos_invoice)
	doc.check_permission("read")
	return build_pos_payload(doc)
