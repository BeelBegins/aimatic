"""Controlled, read-only, whitelist-only query fallback for the AI assistant -
used ONLY when none of the 16 fixed tools in tools.py/tools_extended.py answer
a question (per spec section 4, "AI-Generated Reports").

SECURITY INVARIANTS - do not weaken any of these without re-reading this block:
  - No SQL string is ever built from LLM-supplied text. Every doctype, field,
    aggregation function, and group-by/order-by column the model requests is
    validated against a fixed Python whitelist BEFORE use; invalid input
    returns a retryable {"error": ...} dict, never gets interpolated anywhere.
  - All data access goes through frappe.db.get_list() - never frappe.db.sql()
    with interpolated strings.
  - Company scoping is never optional or LLM-controlled - always resolved
    server-side via tools._resolve_company() and force-applied.
  - Branch scoping is force-applied via tools._resolve_branch_filter() for any
    branch-restricted user, on any doctype that carries a branch field.
  - frappe.has_permission(doctype, "read") is checked and enforced (raises)
    before any query - the one case in this file that's allowed to raise
    rather than return an error dict, since it's a real permission violation.
  - Row limit is always server-enforced (default 50, hard max 200).
  - Any doctype with a date field defaults to the last 90 days when no
    date_from/date_to is given - queries are never unbounded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import frappe
from frappe.utils import add_days, getdate, today

from aimatic.ai.tools import _resolve_branch_filter, _resolve_company

ALLOWED_AGGREGATIONS = {"sum", "count", "avg", "min", "max"}


@dataclass(frozen=True)
class DoctypeConfig:
	fields: frozenset[str]
	date_field: str | None
	company_field: str | None
	branch_field: str | None
	mandatory_filters: dict
	groupable: frozenset[str]
	aggregatable: frozenset[str]


# Sales Invoice Item / Sales Invoice is deliberately excluded - see the
# "Revenue double-counting gotcha" documented elsewhere in this codebase.
# POS Invoice alone is the complete, correct source of POS revenue on this
# bench; unioning or substituting Sales Invoice for it double-counts every
# sale that's gone through a shift close.
_ALLOWED_DOCTYPES: dict[str, DoctypeConfig] = {
	"POS Invoice": DoctypeConfig(
		fields=frozenset(
			{
				"name",
				"posting_date",
				"customer",
				"branch",
				"pos_profile",
				"grand_total",
				"is_return",
				"docstatus",
			}
		),
		date_field="posting_date",
		company_field="company",
		branch_field="branch",
		mandatory_filters={"docstatus": 1},
		groupable=frozenset({"branch", "customer", "pos_profile", "is_return"}),
		aggregatable=frozenset({"grand_total"}),
	),
	"Purchase Invoice": DoctypeConfig(
		fields=frozenset(
			{
				"name",
				"posting_date",
				"supplier",
				"branch",
				"base_grand_total",
				"outstanding_amount",
				"is_return",
				"docstatus",
			}
		),
		date_field="posting_date",
		company_field="company",
		branch_field="branch",
		mandatory_filters={"docstatus": 1},
		groupable=frozenset({"branch", "supplier", "is_return"}),
		aggregatable=frozenset({"base_grand_total", "outstanding_amount"}),
	),
	"Purchase Receipt": DoctypeConfig(
		fields=frozenset(
			{"name", "posting_date", "supplier", "branch", "base_grand_total", "is_return", "docstatus"}
		),
		date_field="posting_date",
		company_field="company",
		branch_field="branch",
		mandatory_filters={"docstatus": 1},
		groupable=frozenset({"branch", "supplier"}),
		aggregatable=frozenset({"base_grand_total"}),
	),
	"Item": DoctypeConfig(
		fields=frozenset({"name", "item_name", "item_group", "brand", "disabled", "custom_mrp"}),
		date_field=None,
		company_field=None,
		branch_field=None,
		mandatory_filters={"disabled": 0},
		groupable=frozenset({"item_group", "brand"}),
		aggregatable=frozenset({"custom_mrp"}),
	),
	"Customer": DoctypeConfig(
		fields=frozenset({"name", "customer_name", "customer_group", "territory", "disabled"}),
		date_field=None,
		company_field=None,
		branch_field=None,
		mandatory_filters={"disabled": 0},
		groupable=frozenset({"customer_group", "territory"}),
		aggregatable=frozenset(),
	),
	"Supplier": DoctypeConfig(
		fields=frozenset({"name", "supplier_name", "supplier_group", "disabled"}),
		date_field=None,
		company_field=None,
		branch_field=None,
		mandatory_filters={"disabled": 0},
		groupable=frozenset({"supplier_group"}),
		aggregatable=frozenset(),
	),
}

_DEFAULT_LOOKBACK_DAYS = 90
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


def run_dynamic_report(
	doctype: str,
	fields: list[str] | None = None,
	aggregate_field: str | None = None,
	aggregate_fn: str | None = None,
	filters: dict | None = None,
	group_by: str | None = None,
	order_by: str | None = None,
	order_dir: str = "desc",
	date_from: str | None = None,
	date_to: str | None = None,
	limit: int = 50,
) -> dict:
	"""Whitelist-validated fallback query, used only when no fixed tool covers
	the question. See the module docstring for the full security model."""
	config = _ALLOWED_DOCTYPES.get(doctype)
	if config is None:
		return {
			"error": f"DocType not available for dynamic reporting: {doctype}. Available: {sorted(_ALLOWED_DOCTYPES)}"
		}

	if not frappe.has_permission(doctype, "read"):
		frappe.throw(f"Not permitted to read {doctype}.", frappe.PermissionError)

	# --- Validate every requested column name against this doctype's own whitelist ---
	requested_fields = fields or []
	for f in requested_fields:
		if f not in config.fields:
			return {
				"error": f"Field '{f}' is not available for dynamic reporting on {doctype}. Allowed: {sorted(config.fields)}"
			}

	if group_by and group_by not in config.groupable:
		return {
			"error": f"Cannot group {doctype} by '{group_by}'. Allowed group-by fields: {sorted(config.groupable)}"
		}

	if order_by and order_by not in config.fields and order_by != "value":
		return {
			"error": f"Cannot order {doctype} by '{order_by}'. Allowed: {sorted(config.fields)} or 'value' when aggregating."
		}

	if order_dir not in ("asc", "desc"):
		order_dir = "desc"

	if aggregate_field is not None or aggregate_fn is not None:
		if aggregate_field not in config.aggregatable:
			return {
				"error": f"Cannot aggregate {doctype}.{aggregate_field}. Allowed: {sorted(config.aggregatable)}"
			}
		if aggregate_fn not in ALLOWED_AGGREGATIONS:
			return {"error": f"Unknown aggregation '{aggregate_fn}'. Allowed: {sorted(ALLOWED_AGGREGATIONS)}"}

	raw_filters = dict(filters or {})
	for key in raw_filters:
		if key not in config.fields:
			return {"error": f"Cannot filter {doctype} on '{key}'. Allowed: {sorted(config.fields)}"}

	# --- Build the actual filter dict passed to frappe.db.get_list, which
	# parameterizes values internally - merging mandatory + forced + user
	# filters. Mandatory/forced filters are applied last so a user-supplied
	# filter can never override them. ---
	merged_filters: dict = dict(raw_filters)
	merged_filters.update(config.mandatory_filters)

	if config.company_field:
		merged_filters[config.company_field] = _resolve_company()

	resolved_date_from = None
	resolved_date_to = None
	if config.branch_field:
		branch_filter = _resolve_branch_filter(_resolve_company(), None)
		if branch_filter is not None and not branch_filter:
			# Same explicit empty-set guard tools.py/tools_extended.py use
			# everywhere else, rather than trusting an ["in", []] filter to
			# behave safely - a branch-restricted user with zero visible
			# branches gets an explicit empty result, not a query attempt.
			return {
				"doctype": doctype,
				"row_count": 0,
				"rows": [],
				"filters_applied": {},
				"date_from": None,
				"date_to": None,
				"limit": limit,
			}
		if branch_filter is not None:
			requested_branch = raw_filters.get(config.branch_field)
			if requested_branch and requested_branch in branch_filter:
				merged_filters[config.branch_field] = requested_branch
			else:
				merged_filters[config.branch_field] = ["in", branch_filter]

	if config.date_field:
		if date_from or date_to:
			try:
				resolved_date_from = getdate(date_from) if date_from else getdate(date_to)
				resolved_date_to = getdate(date_to) if date_to else resolved_date_from
			except Exception:
				return {"error": f"Invalid date_from/date_to: {date_from!r} / {date_to!r}"}
		else:
			resolved_date_to = getdate(today())
			resolved_date_from = add_days(resolved_date_to, -_DEFAULT_LOOKBACK_DAYS)
		merged_filters[config.date_field] = ["between", [resolved_date_from, resolved_date_to]]

	limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))

	# --- Build the SELECT field list. aggregate_fn/aggregate_field are both
	# already validated above against fixed enums (ALLOWED_AGGREGATIONS,
	# config.aggregatable) before being placed in this dict - frappe.db.get_list
	# itself additionally rejects any raw SQL-function *string* in `fields`
	# (confirmed live: "SQL functions are not allowed as strings in SELECT...
	# Use dict syntax like {'COUNT': '*'} instead."), so the dict form below
	# is both required by Frappe's own query builder and independently safe
	# from this file's side, since aggregate_fn/aggregate_field can never be
	# arbitrary LLM text by the time execution reaches here. Frappe's own
	# FUNCTION_MAPPING (database/query.py) is a second, independent whitelist
	# of the exact same function names - do not "simplify" this by accepting
	# a raw fn/field string past validation. ---
	if aggregate_field and aggregate_fn:
		select_fields = [{aggregate_fn.upper(): aggregate_field, "as": "value"}]
		if group_by:
			select_fields.append(group_by)
	else:
		select_fields = requested_fields or (["name"] + [f for f in sorted(config.fields) if f != "name"][:4])

	order_by_clause = f"{order_by} {order_dir}" if order_by else None

	try:
		rows = frappe.db.get_list(
			doctype,
			filters=merged_filters,
			fields=select_fields,
			group_by=group_by,
			order_by=order_by_clause,
			limit_page_length=limit,
			as_list=False,
		)
	except Exception as e:
		return {"error": f"Query failed: {e}"}

	return {
		"doctype": doctype,
		"row_count": len(rows),
		"rows": rows,
		"filters_applied": {k: str(v) for k, v in merged_filters.items()},
		"date_from": str(resolved_date_from) if resolved_date_from else None,
		"date_to": str(resolved_date_to) if resolved_date_to else None,
		"limit": limit,
	}


TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "run_dynamic_report",
			"description": (
				"Fallback controlled query for questions none of the other tools answer directly. "
				"ALWAYS prefer a purpose-built tool over this one when it fits the question - those are "
				"cheaper and more accurate. Only available DocTypes: POS Invoice, Purchase Invoice, "
				"Purchase Receipt, Item, Customer, Supplier, with a fixed whitelist of fields per DocType "
				"(the tool will return an error listing exactly what's allowed if you request something "
				"outside it - read the error and retry with valid field names)."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"doctype": {
						"type": "string",
						"enum": sorted(_ALLOWED_DOCTYPES),
						"description": "The DocType to query.",
					},
					"fields": {
						"type": "array",
						"items": {"type": "string"},
						"description": "Plain fields to return (ignored if aggregate_field/aggregate_fn are given).",
					},
					"aggregate_field": {
						"type": "string",
						"description": "Field to aggregate, e.g. grand_total.",
					},
					"aggregate_fn": {
						"type": "string",
						"enum": sorted(ALLOWED_AGGREGATIONS),
						"description": "Aggregation function to apply to aggregate_field.",
					},
					"filters": {
						"type": "object",
						"description": 'Simple equality filters, e.g. {"supplier": "ABC Traders"}. Range/comparison filters aren\'t supported yet.',
					},
					"group_by": {"type": "string", "description": "Field to group by when aggregating."},
					"order_by": {
						"type": "string",
						"description": "Field to sort by (or 'value' when aggregating).",
					},
					"order_dir": {
						"type": "string",
						"enum": ["asc", "desc"],
						"description": "Sort direction, default desc.",
					},
					"date_from": {
						"type": "string",
						"description": "Start date YYYY-MM-DD, for DocTypes with a date field. Defaults to 90 days ago if omitted.",
					},
					"date_to": {
						"type": "string",
						"description": "End date YYYY-MM-DD. Defaults to today if omitted.",
					},
					"limit": {"type": "integer", "description": "Max rows, default 50, max 200."},
				},
				"required": ["doctype"],
			},
		},
	}
]

DYNAMIC_REPORT_DISPATCH = {"run_dynamic_report": run_dynamic_report}
