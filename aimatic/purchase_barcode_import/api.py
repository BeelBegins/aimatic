"""Batch-resolve barcodes already pasted on purchase document item rows."""

from __future__ import annotations

import json

import frappe

from aimatic.barcode_utils import barcode_variants


def _normalize_barcode_list(barcodes) -> list[str]:
	if isinstance(barcodes, str):
		barcodes = json.loads(barcodes or "[]")

	seen = set()
	ordered: list[str] = []
	for raw in barcodes or []:
		barcode = str(raw or "").strip()
		if not barcode or barcode in seen:
			continue
		seen.add(barcode)
		ordered.append(barcode)
	return ordered


def _parents_by_barcode_value(values: list[str]) -> dict[str, list[str]]:
	"""Map stored barcode string → ordered unique Item parents."""
	if not values:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT barcode, parent
		FROM `tabItem Barcode`
		WHERE barcode IN %(values)s
		ORDER BY parent ASC, idx ASC
		""",
		{"values": values},
		as_dict=True,
	)

	by_value: dict[str, list[str]] = {}
	for row in rows:
		key = row["barcode"]
		parent = row["parent"]
		parents = by_value.setdefault(key, [])
		if parent not in parents:
			parents.append(parent)
	return by_value


def _resolve_one(barcode: str, parents_by_value: dict[str, list[str]]) -> list[str]:
	"""Resolve one pasted barcode via GTIN variants, then item_code fallback."""
	item_codes: list[str] = []
	for variant in barcode_variants(barcode):
		for parent in parents_by_value.get(variant, []):
			if parent not in item_codes:
				item_codes.append(parent)

	if not item_codes and frappe.db.exists("Item", barcode):
		item_codes = [barcode]

	return item_codes


@frappe.whitelist()
def resolve_barcodes_for_purchase(barcodes=None):
	"""Map pasted barcodes to Item codes for Purchase Order / Receipt grids.

	Returns:
	  matched: {barcode: item_code}
	  unmatched: [barcode, ...]
	  ambiguous: [{barcode, item_codes}, ...]
	  disabled: [{barcode, item_code}, ...]
	"""
	frappe.has_permission("Item", ptype="read", throw=True)

	ordered = _normalize_barcode_list(barcodes)
	if not ordered:
		return {"matched": {}, "unmatched": [], "ambiguous": [], "disabled": []}

	all_variants: list[str] = []
	seen_variants = set()
	for barcode in ordered:
		for variant in barcode_variants(barcode):
			if variant not in seen_variants:
				seen_variants.add(variant)
				all_variants.append(variant)

	parents_by_value = _parents_by_barcode_value(all_variants)

	matched: dict[str, str] = {}
	unmatched: list[str] = []
	ambiguous: list[dict] = []
	disabled: list[dict] = []

	for barcode in ordered:
		item_codes = _resolve_one(barcode, parents_by_value)
		if not item_codes:
			unmatched.append(barcode)
			continue
		if len(item_codes) > 1:
			ambiguous.append({"barcode": barcode, "item_codes": item_codes})
			continue

		item_code = item_codes[0]
		if frappe.get_cached_value("Item", item_code, "disabled"):
			disabled.append({"barcode": barcode, "item_code": item_code})
			continue

		matched[barcode] = item_code

	return {
		"matched": matched,
		"unmatched": unmatched,
		"ambiguous": ambiguous,
		"disabled": disabled,
	}
