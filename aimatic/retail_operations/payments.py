"""Prospective payment voucher notifications; no network calls in financial hooks."""

from urllib.parse import quote

import frappe

from aimatic.retail_operations.calculations import fingerprint
from aimatic.retail_operations.discord import operations_url, route
from aimatic.retail_operations.presentation import card, field, number, payload, text
from aimatic.retail_operations.service import settings, write_internal

EVENTS = {"after_insert": "Created", "on_submit": "Submitted", "on_cancel": "Cancelled"}


def payment_card(doc, branch, event):
	state = {0: "Draft — not posted", 1: "Submitted in ERPNext", 2: "Cancelled"}[int(doc.docstatus)]
	url = operations_url().split("/app/")[0] + "/app/payment-entry/" + quote(doc.name, safe="")
	e = card(
		"Payment voucher | " + event,
		"Accounts",
		f"**{text(branch.name)}**\n{state}. This event does not confirm bank settlement.",
		url,
	)
	if event == "Cancelled":
		e["color"] = 0xDC2626
	field(e, "Voucher", text(doc.name, 140))
	field(e, "Type", text(doc.payment_type), True)
	field(e, "Posting date", str(doc.posting_date), True)
	field(e, "Event time · PKT", str(frappe.utils.now_datetime())[:19])
	field(e, "Party", text(doc.party_name or doc.party or "Internal transfer", 180))
	field(
		e,
		"Paid / source amount",
		f"{text(doc.paid_from_account_currency, 15)} **{number(doc.paid_amount)}**",
		True,
	)
	field(
		e,
		"Received / destination amount",
		f"{text(doc.paid_to_account_currency, 15)} **{number(doc.received_amount)}**",
		True,
	)
	field(e, "Payment method", text(doc.mode_of_payment))
	references = doc.get("references") or []
	if references:
		field(
			e,
			"Linked documents",
			"\n".join(text(r.reference_doctype + ": " + r.reference_name, 150) for r in references[:5])
			+ (f"\n+{len(references) - 5} more in the voucher" if len(references) > 5 else ""),
		)
	field(e, "Recorded by", text(doc.owner if event == "Created" else frappe.session.user, 140))
	return payload(e, 1, 1)


def payment_event(doc, method=None):
	"""Create one durable event per lifecycle step in the same DB transaction."""
	if doc.doctype != "Payment Entry" or method not in EVENTS:
		return
	if not frappe.db.table_exists("Retail Discord Delivery"):
		return
	# Notification errors must not prevent the underlying financial save.
	frappe.db.savepoint("retail_payment_alert")
	try:
		config = settings()
		if not config.enabled or not config.discord_enabled:
			return
		name = doc.get("branch")
		if not name and doc.get("cost_center"):
			matches = frappe.get_all(
				"Branch",
				filters={"company": doc.company, "cost_center": doc.cost_center},
				pluck="name",
				limit_page_length=2,
			)
			name = matches[0] if len(matches) == 1 else None
		if not name:
			raise ValueError("Missing or ambiguous branch")
		branch = frappe.get_doc("Branch", name)
		if branch.company != doc.company or (
			doc.get("cost_center") and branch.cost_center != doc.cost_center
		):
			raise ValueError("Branch and company/cost center disagree")
		if not branch.get("custom_operations_enabled"):
			return
		address = route(branch.name, "Accounts")
		if not address:
			raise ValueError("Accounts destination missing")
		key = fingerprint(["payment-voucher", doc.doctype, doc.name, method])
		if frappe.db.exists("Retail Discord Delivery", {"delivery_key": key}):
			return
		write_internal(
			frappe.get_doc(
				dict(
					doctype="Retail Discord Delivery",
					delivery_key=key,
					channel="Accounts",
					route_branch=branch.name,
					branches=frappe.as_json([branch.name]),
					destination_hash=fingerprint(address),
					payload=frappe.as_json(payment_card(doc, branch, EVENTS[method])),
					status="Pending",
				)
			)
		)
	except Exception:
		frappe.db.rollback(save_point="retail_payment_alert")
		try:
			frappe.log_error(
				title="Payment voucher alert not queued",
				message=f"{doc.doctype} {doc.name}: verify branch/company/cost center, Accounts destination and Retail Discord Delivery. The financial transaction was not blocked.",
			)
		except Exception:
			pass
		return
	# Existing minute delivery drain is the durable fallback; no Redis dependency here.
