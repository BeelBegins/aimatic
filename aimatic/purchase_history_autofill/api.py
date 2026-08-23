import json

import frappe

from aimatic.purchase_history_autofill.utils import fetch_latest_history_rows, get_autofillable_fields


def _preview_history_map(supplier: str, branch: str, item_codes, target_doctype: str):
	if not (supplier and branch and item_codes):
		return {}

	fieldtypes = get_autofillable_fields(target_doctype)
	if not fieldtypes:
		return {}

	return fetch_latest_history_rows(supplier, branch, item_codes, fieldtypes)


@frappe.whitelist()
def preview_item_history(
	supplier: str, branch: str, item_code: str, target_doctype: str = "Purchase Receipt Item"
):
	"""Live preview for purchase forms: same matching logic as the
	before_validate hooks (aimatic.purchase_history_autofill.events),
	exposed read-only so the client can pre-fill blank grid cells before
	save. Convenience only - the before_validate hook is the guarantee.
	"""
	if not item_code:
		return {}

	history_map = _preview_history_map(supplier, branch, [item_code], target_doctype)
	return history_map.get(item_code, {})


@frappe.whitelist()
def preview_items_history(
	supplier: str,
	branch: str,
	item_codes=None,
	target_doctype: str = "Purchase Receipt Item",
):
	"""Batch live preview for the items grid. Returns
	{item_code: {fieldname: value, ...}, ...} plus fieldtypes so the client
	can mirror the server-side empty/zero rules (Check fields keep 0).
	"""
	if isinstance(item_codes, str):
		item_codes = json.loads(item_codes or "[]")

	history_map = _preview_history_map(supplier, branch, item_codes or [], target_doctype)
	fieldtypes = get_autofillable_fields(target_doctype)
	return {"history": history_map, "fieldtypes": fieldtypes}
