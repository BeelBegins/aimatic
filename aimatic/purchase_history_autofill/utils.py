"""Shared logic for auto-filling Purchase Receipt Item fields from the most
recent submitted Purchase Receipt/Purchase Invoice row for the same
Supplier + Branch + Item. Used by both events.py (the before_validate hook,
the actual guarantee) and api.py (an optional live client-side preview).

Design notes:
  - Quantity and Rate (and anything the client-side tax-calc script derives
    rate from, e.g. custom_vendor_rate) are never touched - see
    _ALWAYS_EXCLUDE below. This is enforced structurally, independent of the
    "only if empty" check, so a bug in that check can't reach these fields.
  - Only the *input* custom fields (percentages, formulas, MRP, trade offer,
    discount) are copied - the *_amount/*_total/read-only computed fields
    are excluded because the existing Purchase Receipt/Purchase Order/
    Purchase Invoice client scripts (prv1 / PurchaseInvoice tax-calc
    scripts) recompute them from those inputs on load - copying the
    computed outputs too would either be immediately overwritten or, worse,
    show a stale number before that recalculation runs.
  - Field discovery is schema-driven (frappe.get_meta), not a hardcoded
    per-site field list, so a custom field that exists on one site but not
    another (rule: "custom fields may differ between sites") is handled
    automatically - it's simply absent from that site's meta.
  - Do NOT wrap get_meta in an extra process-level cache (e.g.
    functools.lru_cache) here: this bench runs szl/hsm/siezal in the same
    gunicorn worker process, and a plain function-level cache keyed only by
    doctype name would leak one site's field set into another site's
    request. frappe.get_meta() already does its own correctly
    site-scoped caching - just call it directly.
"""

from __future__ import annotations

import frappe

# Fieldtypes that can hold a genuinely copyable purchase-condition value.
# Section Break/Column Break/Table (layout-only) are excluded implicitly by
# not being in this set.
_COPYABLE_FIELDTYPES = {
	"Currency",
	"Percent",
	"Float",
	"Int",
	"Check",
	"Data",
	"Small Text",
	"Link",
	"Select",
}

# Standard ERPNext / Accounting Dimension fields that are always doc-specific
# (rule 7) or represent Quantity/Rate (rule 1) - never eligible for
# history-based autofill, regardless of custom-field configuration.
_DOC_SPECIFIC_DENY = frozenset(
	{
		# identity / structural
		"item_code",
		"item_name",
		"description",
		"image",
		"name",
		"parent",
		"parentfield",
		"parenttype",
		"idx",
		"docstatus",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"doctype",
		# quantity - rule 1, plus inherently doc-specific
		"qty",
		"stock_qty",
		"uom",
		"stock_uom",
		"conversion_factor",
		"stock_uom_rate",
		"received_qty",
		"rejected_qty",
		"returned_qty",
		"billed_amt",
		# rate/amount - rule 1, plus everything core ERPNext derives from it
		"rate",
		"amount",
		"price_list_rate",
		"base_rate",
		"base_amount",
		"base_net_rate",
		"base_net_amount",
		"net_rate",
		"net_amount",
		"discount_percentage",
		"discount_amount",
		"distributed_discount_amount",
		"margin_type",
		"margin_rate_or_amount",
		"rate_with_margin",
		"base_rate_with_margin",
		# warehouse / accounting dimensions / doc-specific linkage - rule 7
		"warehouse",
		"rejected_warehouse",
		"from_warehouse",
		"t_warehouse",
		"s_warehouse",
		"cost_center",
		"project",
		"branch",
		"batch_no",
		"serial_no",
		"serial_and_batch_bundle",
		"expense_account",
		"item_tax_template",
		"weight_per_unit",
		"total_weight",
		"weight_uom",
		"purchase_order",
		"purchase_order_item",
		"material_request",
		"material_request_item",
		"sales_order",
		"sales_order_item",
		"subcontracted_item",
		"bom",
		"include_exploded_items",
		"allow_zero_valuation_rate",
		"landed_cost_voucher_amount",
		"rm_supp_cost",
		"asset_location",
		"asset_category",
		"quality_inspection",
		"putaway_rule",
		"manufacturer",
		"manufacturer_part_no",
		"brand",
		"item_group",
		"page_break",
		"barcode",
		"is_free_item",
		"retain_sample",
		"sample_quantity",
	}
)

# aimatic custom fields that are read-only / computed by the existing
# client-side tax-calc scripts from the *_formula and *_per input fields -
# never populate these directly (see module docstring).
_COMPUTED_OUTPUT_DENY = frozenset(
	{
		"custom_price_after_taxes",
		"custom_price_without_taxes",
		"custom_old_price_after_tax",
		"custom_discount_amnt",
		"custom_advance_tax_amount",
		"custom_gst_amount",
		"custom_fed_amount",
		"custom_trade_offer_total",
		"custom_gross_total",
		"custom_line_total",
		"custom_base_cost_price",
		"custom_gst_3rd_schedule",
	}
)

# aimatic custom fields that are themselves quantity variants, not purchase
# conditions - copying them would violate rule 1's spirit even though they
# aren't literally named "qty"/"rate".
_QTY_LINKED_CUSTOM_DENY = frozenset({"custom_inventory_qty", "custom_scheme_qty"})

# Belt-and-suspenders: custom_vendor_rate is the field the client-side
# tax-calc scripts treat as the purchase rate input (rate itself is
# read-only on Purchase Receipt Item, computed from this) - explicitly
# excluded even though it would already be non-empty in the normal
# Get-Items-From-PO flow (matching fieldnames copy automatically).
_RATE_LINKED_CUSTOM_DENY = frozenset({"custom_vendor_rate"})

# custom_fp_price has its own dedicated prefill mechanism (a live client
# script that pulls the branch's *current* Foodpanda Price List rate - see
# public/js/foodpanda_price_prefill.js) which is a fundamentally different
# source than this module's "carry forward the last submitted document's
# value" - mixing both onto the same field would fight over which one wins.
_CURRENT_PRICE_LIST_DENY = frozenset({"custom_fp_price"})

_ALWAYS_EXCLUDE = (
	_DOC_SPECIFIC_DENY
	| _COMPUTED_OUTPUT_DENY
	| _QTY_LINKED_CUSTOM_DENY
	| _RATE_LINKED_CUSTOM_DENY
	| _CURRENT_PRICE_LIST_DENY
)

# Doctypes searched for history, in the order rule 3 lists them. Both are
# unioned and the single most recent matching row wins regardless of which
# doctype it came from.
_SOURCE_DOCTYPES = ("Purchase Receipt", "Purchase Invoice")


def get_autofillable_fields(target_doctype: str) -> dict[str, str]:
	"""Returns {fieldname: fieldtype} for every custom field on
	target_doctype that is eligible for history-based autofill - i.e. every
	custom_* field of a scalar fieldtype, minus the deny-lists above.
	Schema-driven per rule 14 (works whether or not a given custom field
	exists on this site) and rule 12 (no hardcoded field list beyond the
	small, documented deny-list).
	"""
	meta = frappe.get_meta(target_doctype)
	fields = {}
	for df in meta.fields:
		if not df.fieldname.startswith("custom_"):
			continue
		if df.fieldtype not in _COPYABLE_FIELDTYPES:
			continue
		if df.fieldname in _ALWAYS_EXCLUDE:
			continue
		fields[df.fieldname] = df.fieldtype
	return fields


def _is_empty(value) -> bool:
	"""Rule 2's "empty, null, or zero" check, applied uniformly."""
	return value in (None, "", 0, 0.0)


def fetch_latest_history_rows(
	supplier: str, branch: str, item_codes, target_fields: dict[str, str]
) -> dict[str, dict]:
	"""For each item_code, finds the single most recent submitted,
	non-return Purchase Receipt/Purchase Invoice row for this exact
	supplier + branch that contains that item (rule 3, 4, 6, 8), searching
	across ALL matching historical rows - not just the latest document - so
	an item missing from the latest purchase naturally falls through to an
	older one that has it (rule 5), with no extra logic needed since the
	query is already row-level, not document-level.

	Batched across every item_code in one query per source doctype (rule 15
	- no N+1), returning only fields whose value is non-empty for that
	fieldtype (nothing meaningful to copy otherwise).
	"""
	item_codes = list({c for c in (item_codes or []) if c})
	if not supplier or not branch or not item_codes or not target_fields:
		return {}

	candidates: dict[str, list] = {}

	for source_doctype in _SOURCE_DOCTYPES:
		child_doctype = f"{source_doctype} Item"
		source_fieldnames = {df.fieldname for df in frappe.get_meta(child_doctype).fields}
		usable_fields = [f for f in target_fields if f in source_fieldnames]
		if not usable_fields:
			continue

		child_table = f"`tab{child_doctype}`"
		parent_table = f"`tab{source_doctype}`"
		select_cols = ", ".join(f"child.`{f}`" for f in usable_fields)

		rows = frappe.db.sql(
			f"""
            SELECT
                child.item_code AS _item_code,
                {select_cols},
                parent.posting_date AS _posting_date,
                parent.posting_time AS _posting_time,
                child.creation AS _creation,
                child.name AS _name,
                child.idx AS _idx
            FROM {child_table} child
            INNER JOIN {parent_table} parent ON parent.name = child.parent
            WHERE parent.docstatus = 1
              AND parent.is_return = 0
              AND parent.supplier = %(supplier)s
              AND parent.branch = %(branch)s
              AND child.item_code IN %(item_codes)s
            """,
			{"supplier": supplier, "branch": branch, "item_codes": item_codes},
			as_dict=True,
		)

		for row in rows:
			sort_key = (
				row._posting_date,
				row._posting_time or "00:00:00",
				row._creation,
				row._name,
				row._idx,
			)
			candidates.setdefault(row._item_code, []).append((sort_key, row, usable_fields))

	result: dict[str, dict] = {}
	for item_code, entries in candidates.items():
		entries.sort(key=lambda pair: pair[0], reverse=True)
		_, best_row, usable_fields = entries[0]
		row_values = {}
		for fieldname in usable_fields:
			fieldtype = target_fields.get(fieldname)
			value = best_row.get(fieldname)
			# Check fields: 0/1 are both meaningful states, so a checked=0
			# history value is still worth copying. For every other
			# fieldtype, a falsy history value means "nothing recorded" -
			# skip it rather than copying a meaningless zero/blank.
			if fieldtype != "Check" and _is_empty(value):
				continue
			row_values[fieldname] = value
		if row_values:
			result[item_code] = row_values

	return result


def apply_history_to_row(row, history: dict) -> list[str]:
	"""Sets each field in history onto row, but only if row's current value
	for that field is empty/null/zero (rule 2) - never overwrites a
	deliberately entered value (rule 2), and Quantity/Rate are never even
	candidates since they're excluded from `history` upstream (rule 1).
	Returns the list of fieldnames actually changed, for logging/testing.
	"""
	changed = []
	for fieldname, value in history.items():
		if not _is_empty(row.get(fieldname)):
			continue
		row.set(fieldname, value)
		changed.append(fieldname)
	return changed
