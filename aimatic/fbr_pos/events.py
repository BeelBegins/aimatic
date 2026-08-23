import frappe
from frappe import _

from aimatic.fbr_pos.accounting import apply_fbr_accounting_rows
from aimatic.fbr_pos.api import submit_pos_invoice_to_fbr
from aimatic.fbr_pos.payload_builder import build_pos_payload


def clear_copied_fbr_fields_for_return(doc):
	"""
	ERPNext copies custom fields from original POS Invoice to return POS Invoice.
	For returns, we must clear copied FBR data so refund is submitted separately.
	"""

	if not getattr(doc, "is_return", 0):
		return

	fields_to_clear = [
		"custom_fbr_response_payload",
		"custom_fbr_submission_date",
		"custom_fbr_request_payload",
		"custom_fbr_invoice_number",
		"custom_fbr_retry_count",
		"custom_fbr_http_status",
		"custom_fbr_error",
		"custom_fbr_usin",
	]

	for fieldname in fields_to_clear:
		if doc.meta.has_field(fieldname):
			doc.set(fieldname, None)

	if doc.meta.has_field("custom_fbr_status"):
		doc.custom_fbr_status = "Not Sent"


def validate_pos_invoice(doc, method=None):
	"""
	Build FBR item snapshot and add ERPNext accounting tax rows.
	Does not call FBR API here.
	"""

	clear_copied_fbr_fields_for_return(doc)

	build_pos_payload(doc)

	apply_fbr_accounting_rows(doc)


def before_submit_pos_invoice(doc, method=None):
	"""
	Submit sale/refund to FBR before final submit.
	"""

	if not getattr(doc, "is_return", 0):
		if getattr(doc, "custom_fbr_status", None) == "Accepted" and getattr(
			doc, "custom_fbr_invoice_number", None
		):
			return

	submit_pos_invoice_to_fbr(doc)


def on_cancel_pos_invoice(doc, method=None):
	if doc.meta.has_field("custom_fbr_status"):
		doc.custom_fbr_status = "Cancelled"
