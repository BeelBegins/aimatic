"""Barcode-variant fallback for the Stock Reconciliation scan field.

Core `erpnext.stock.utils.scan_barcode` matches `search_value` against
`Item Barcode.barcode` exactly. Handheld scanners commonly drop or add the
one leading zero that separates UPC-A (12 digits) from EAN-13 (13 digits),
so a scan of an item whose barcode is stored in the other format silently
fails to auto-pick the row. `aimatic.barcode_utils.barcode_variants` already
solves this for POS/price-check/import scan paths; this wires the same
fallback into Stock Reconciliation's scan without touching ERPNext core.
"""

from __future__ import annotations

import frappe
from erpnext.stock.utils import scan_barcode as _core_scan_barcode

from aimatic.barcode_utils import barcode_variants


@frappe.whitelist()
def scan_barcode(search_value: str, ctx=None):
	result = _core_scan_barcode(search_value=search_value, ctx=ctx)
	if result:
		return result

	for variant in barcode_variants(search_value):
		if variant == search_value:
			continue
		result = _core_scan_barcode(search_value=variant, ctx=ctx)
		if result:
			return result

	return result
