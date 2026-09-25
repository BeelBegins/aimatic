from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, now_datetime

from aimatic.branch_management.utils import get_user_default_branch, get_warehouse_branch, user_can_override

RECEIVING_ROLES = {"Stock User", "Stock Manager", "Purchase User", "Purchase Manager", "System Manager"}
SOURCE_TYPES = {"Stock Entry", "Purchase Receipt"}


def _require_receiver():
	if frappe.session.user == "Guest":
		frappe.throw(_("Sign in is required."), frappe.PermissionError)
	if not set(frappe.get_roles()).intersection(RECEIVING_ROLES):
		frappe.throw(_("Your user is not allowed to receive stock."), frappe.PermissionError)
	return frappe.session.user


def _visible_branches() -> set[str]:
	if user_can_override():
		return set(frappe.get_list("Branch", pluck="name", limit_page_length=500))
	branch = get_user_default_branch()
	if not branch:
		frappe.throw(_("Assign a default Branch to your user before using Stock Receiving."), frappe.PermissionError)
	return {branch}


def _branch_for_source(source_type: str, doc) -> tuple[str | None, str | None]:
	if source_type == "Purchase Receipt":
		warehouse = doc.get("set_warehouse") or next((row.get("warehouse") for row in doc.items if row.get("warehouse")), None)
		return doc.get("branch") or get_warehouse_branch(warehouse), warehouse
	warehouses = {row.get("t_warehouse") for row in doc.items if row.get("t_warehouse")}
	branches = {get_warehouse_branch(warehouse) for warehouse in warehouses}
	branches.discard(None)
	if len(branches) != 1:
		return None, next(iter(warehouses), None)
	return next(iter(branches)), next(iter(warehouses), None)


def _assert_source_access(source_type: str, name: str, *, require_draft=False):
	if source_type not in SOURCE_TYPES:
		frappe.throw(_("Unsupported receiving source."))
	doc = frappe.get_doc(source_type, name)
	if require_draft and doc.docstatus != 0:
		frappe.throw(_("Purchase Receipt {0} is no longer a draft.").format(frappe.bold(name)))
	if not require_draft and doc.docstatus != 1:
		frappe.throw(_("Stock Transfer Note {0} must be submitted first.").format(frappe.bold(name)))
	branch, warehouse = _branch_for_source(source_type, doc)
	if not branch or branch not in _visible_branches():
		frappe.throw(_("This document is outside your receiving branch."), frappe.PermissionError)
	return doc, branch, warehouse


def _source_row(row, source_type: str) -> dict[str, Any]:
	barcodes = frappe.get_all("Item Barcode", filters={"parent": row.item_code, "parenttype": "Item"}, pluck="barcode", limit_page_length=20)
	return {"source_row": row.name, "item_code": row.item_code, "item_name": row.item_name, "uom": row.uom, "barcodes": barcodes, "expected_qty": flt(row.qty), "received_qty": flt(row.qty), "damaged_qty": 0, "batch_no": row.get("batch_no") if source_type == "Purchase Receipt" else None, "expiry_date": None}


def _already_received(source_type: str, name: str):
	return frappe.db.get_value("Stock Receiving", {"source_type": source_type, "source_name": name, "docstatus": 1}, ["name", "status"], as_dict=True)


@frappe.whitelist(allow_guest=True)
def get_public_config():
	client = frappe.db.get_value("OAuth Client", {"app_name": "Aimatic Stock Receiving Android"}, ["name", "default_redirect_uri"], as_dict=True)
	if not client:
		frappe.throw(_("Stock Receiving OAuth is not configured"))
	return {"oauth_client_id": client.name, "redirect_uri": client.default_redirect_uri, "scope": "stock-receiving"}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_context():
	user = _require_receiver()
	return {"user": user, "full_name": frappe.utils.get_fullname(user), "branches": sorted(_visible_branches()), "can_create_purchase_receipt": bool(set(frappe.get_roles()).intersection({"Purchase User", "Purchase Manager", "System Manager"}))}


def _summary(source_type: str, doc, branch: str, warehouse: str | None) -> dict[str, Any]:
	existing = _already_received(source_type, doc.name)
	return {"source_type": source_type, "name": doc.name, "title": doc.name, "branch": branch, "warehouse": warehouse, "supplier": doc.get("supplier") if source_type == "Purchase Receipt" else None, "date": str(doc.get("posting_date") or doc.get("transaction_date") or doc.get("creation") or ""), "item_count": len(doc.items), "status": existing.status if existing else "Pending"}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def list_pending(limit=200):
	_require_receiver()
	visible = _visible_branches()
	rows: list[dict[str, Any]] = []
	for source_type, filters in (("Stock Entry", {"docstatus": 1, "purpose": "Material Transfer"}), ("Purchase Receipt", {"docstatus": 0})):
		for name in frappe.get_all(source_type, filters=filters, pluck="name", order_by="modified desc", limit_page_length=cint(limit)):
			doc = frappe.get_doc(source_type, name)
			branch, warehouse = _branch_for_source(source_type, doc)
			if branch not in visible or _already_received(source_type, name):
				continue
			rows.append(_summary(source_type, doc, branch, warehouse))
	rows.sort(key=lambda row: row.get("date") or "", reverse=True)
	return {"documents": rows[:cint(limit)]}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_document(source_type: str, name: str):
	_require_receiver()
	doc, branch, warehouse = _assert_source_access(source_type, name, require_draft=source_type == "Purchase Receipt")
	existing = _already_received(source_type, name)
	return {**_summary(source_type, doc, branch, warehouse), "items": [_source_row(row, source_type) for row in doc.items], "already_received": bool(existing)}


def _ensure_batch(item_code: str, batch_no: str | None, expiry_date: str | None):
	item = frappe.db.get_value("Item", item_code, ["has_batch_no", "has_expiry_date"], as_dict=True)
	if not item or not item.has_batch_no:
		return
	if not batch_no:
		frappe.throw(_("Batch number is required for item {0}.").format(frappe.bold(item_code)))
	existing = frappe.db.get_value("Batch", batch_no, ["item", "expiry_date"], as_dict=True)
	if existing:
		if existing.item != item_code:
			frappe.throw(_("Batch {0} belongs to another item.").format(frappe.bold(batch_no)))
		if expiry_date and existing.expiry_date and str(existing.expiry_date) != str(expiry_date):
			frappe.throw(_("Batch {0} already has a different expiry date.").format(frappe.bold(batch_no)))
		return
	frappe.get_doc({"doctype": "Batch", "batch_id": batch_no, "item": item_code, "expiry_date": expiry_date}).insert()


def _normalise_items(doc, source_type: str, items):
	if not isinstance(items, list) or not items:
		frappe.throw(_("At least one receiving item is required."))
	by_name = {row.name: row for row in doc.items}
	result = []
	seen = set()
	for raw in items:
		if not isinstance(raw, dict):
			frappe.throw(_("Receiving rows must be objects."))
		row_name = str(raw.get("source_row") or "")
		row = by_name.get(row_name)
		if not row or row_name in seen:
			frappe.throw(_("A receiving row does not belong to this document."))
		seen.add(row_name)
		received_qty = flt(raw.get("received_qty"))
		damaged_qty = flt(raw.get("damaged_qty"))
		if received_qty < 0 or damaged_qty < 0:
			frappe.throw(_("Quantities cannot be negative."))
		if source_type == "Stock Entry" and received_qty > flt(row.qty):
			frappe.throw(_("Received quantity cannot exceed the transferred quantity."))
		batch_no = str(raw.get("batch_no") or "").strip() or None
		expiry_date = str(raw.get("expiry_date") or "").strip() or None
		if source_type == "Stock Entry":
			batch_no = None
			expiry_date = None
		result.append({"source_row": row.name, "item_code": row.item_code, "item_name": row.item_name, "uom": row.uom, "expected_qty": flt(row.qty), "received_qty": received_qty, "damaged_qty": damaged_qty, "batch_no": batch_no, "expiry_date": expiry_date, "remarks": str(raw.get("remarks") or "")[:500]})
	if len(seen) != len(by_name):
		frappe.throw(_("Every source item must be checked before submission."))
	return result


@frappe.whitelist()
@rate_limit(limit=20, seconds=60)
def submit_receiving(source_type: str, name: str, items, remarks="", idempotency_key=""):
	user = _require_receiver()
	if isinstance(items, str):
		items = frappe.parse_json(items)
	key = str(idempotency_key or "").strip()
	if key:
		existing = frappe.db.get_value("Stock Receiving", {"idempotency_key": key}, "name")
		if existing:
			return {"name": existing, "status": frappe.db.get_value("Stock Receiving", existing, "status"), "duplicate": True}
	doc, branch, warehouse = _assert_source_access(source_type, name, require_draft=source_type == "Purchase Receipt")
	if _already_received(source_type, name):
		frappe.throw(_("This document has already been received."))
	normalised = _normalise_items(doc, source_type, items)
	if source_type == "Purchase Receipt":
		for received in normalised:
			row = next(row for row in doc.items if row.name == received["source_row"])
			row.qty = received["received_qty"]
			if received["batch_no"] and row.meta.has_field("batch_no"):
				row.batch_no = received["batch_no"]
			_ensure_batch(received["item_code"], received["batch_no"], received["expiry_date"])
		# Receiving verifies the draft quantities; Purchase Manager owns final PR submission.
		doc.save()
	status = "Received" if all(flt(row["received_qty"]) >= flt(row["expected_qty"]) and not flt(row["damaged_qty"]) for row in normalised) else "Disputed"
	receiving = frappe.get_doc({"doctype": "Stock Receiving", "source_type": source_type, "source_name": name, "receiving_branch": branch, "receiving_warehouse": warehouse, "status": status, "receiver": user, "received_at": now_datetime(), "idempotency_key": key or None, "remarks": str(remarks or "")[:1000], "items": normalised})
	receiving.insert()
	receiving.submit()
	return {"name": receiving.name, "source_name": name, "status": status, "duplicate": False}
