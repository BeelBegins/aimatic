"""Shared data layer for the Branch Transfer Summary and Detail script reports.

A branch-to-branch movement is one Stock Entry Detail row of a submitted
Material Transfer whose source and target warehouses belong to different
branches (via Warehouse.custom_branch).  Both reports read the same rows, so
the summary always reconciles to the detail.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate

# Refused up front rather than returning an unreadable grid - narrow the
# filters (dates, branch, item group) instead.
MAX_ROWS = 50_000


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date"))


def _permitted_branches() -> list[str] | None:
	"""None = unrestricted; otherwise the user's Branch User Permissions."""
	branch_perms = frappe.permissions.get_user_permissions(frappe.session.user).get("Branch")
	if not branch_perms:
		return None
	return [p.doc for p in branch_perms]


def get_transfer_rows(filters) -> list[frappe._dict]:
	"""One row per Stock Entry Detail line moving stock between two branches."""
	conditions = [
		"se.`docstatus` = 1",
		"se.`purpose` = 'Material Transfer'",
		"se.`company` = %(company)s",
		"se.`posting_date` BETWEEN %(from_date)s AND %(to_date)s",
		"wf.`custom_branch` IS NOT NULL",
		"wt.`custom_branch` IS NOT NULL",
		"wf.`custom_branch` != wt.`custom_branch`",
	]
	params = {
		"company": filters.company,
		"from_date": filters.from_date,
		"to_date": filters.to_date,
		"limit": MAX_ROWS + 1,
	}

	for fieldname, sql_field in (
		("from_branch", "wf.`custom_branch`"),
		("to_branch", "wt.`custom_branch`"),
		("item_group", "item.`item_group`"),
		("item_code", "sed.`item_code`"),
	):
		if filters.get(fieldname):
			conditions.append(f"{sql_field} = %({fieldname})s")
			params[fieldname] = filters[fieldname]

	permitted = _permitted_branches()
	if permitted is not None:
		conditions.append("(wf.`custom_branch` IN %(permitted)s OR wt.`custom_branch` IN %(permitted)s)")
		params["permitted"] = permitted

	# nosemgrep
	rows = frappe.db.sql(
		f"""
		SELECT
			se.`name` AS stock_entry, se.`posting_date`, se.`owner` AS created_by,
			sed.`item_code`, sed.`item_name`, item.`item_group`,
			sed.`uom`, sed.`qty`, sed.`transfer_qty`, sed.`basic_rate`, sed.`amount`,
			sed.`s_warehouse` AS from_warehouse, wf.`custom_branch` AS from_branch,
			sed.`t_warehouse` AS to_warehouse, wt.`custom_branch` AS to_branch
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.`parent` = se.`name`
		INNER JOIN `tabWarehouse` wf ON wf.`name` = sed.`s_warehouse`
		INNER JOIN `tabWarehouse` wt ON wt.`name` = sed.`t_warehouse`
		INNER JOIN `tabItem` item ON item.`name` = sed.`item_code`
		WHERE {" AND ".join(conditions)}
		ORDER BY se.`posting_date`, se.`name`, sed.`idx`
		LIMIT %(limit)s
		""",
		params,
		as_dict=True,
	)

	if len(rows) > MAX_ROWS:
		frappe.throw(
			_("More than {0} transfer lines match. Narrow the dates, branches or item group first.").format(
				MAX_ROWS
			)
		)
	return [frappe._dict(row) for row in rows]


def summarise(rows: list[frappe._dict]) -> list[frappe._dict]:
	"""Roll detail rows up per source branch -> target branch pair."""
	pairs: dict[tuple[str, str], frappe._dict] = {}
	for row in rows:
		pair = pairs.setdefault(
			(row.from_branch, row.to_branch),
			frappe._dict(
				from_branch=row.from_branch,
				to_branch=row.to_branch,
				stock_entries=set(),
				item_codes=set(),
				qty=0.0,
				value=0.0,
				first_date=row.posting_date,
				last_date=row.posting_date,
			),
		)
		pair.stock_entries.add(row.stock_entry)
		pair.item_codes.add(row.item_code)
		pair.qty += flt(row.transfer_qty)
		pair.value += flt(row.amount)
		pair.first_date = min(pair.first_date, row.posting_date)
		pair.last_date = max(pair.last_date, row.posting_date)

	result = []
	for pair in pairs.values():
		pair.transfers = len(pair.pop("stock_entries"))
		pair.item_count = len(pair.pop("item_codes"))
		result.append(pair)
	result.sort(key=lambda p: (-p.value, p.from_branch, p.to_branch))
	return result


def get_summary_cards(rows: list[frappe._dict], currency: str | None) -> list[dict]:
	return [
		{
			"label": _("Transfers"),
			"value": len({row.stock_entry for row in rows}),
			"indicator": "Blue",
			"datatype": "Int",
		},
		{
			"label": _("Items"),
			"value": len({row.item_code for row in rows}),
			"indicator": "Blue",
			"datatype": "Int",
		},
		{
			"label": _("Qty (Stock UOM)"),
			"value": flt(sum(flt(row.transfer_qty) for row in rows), 3),
			"indicator": "Green",
			"datatype": "Float",
		},
		{
			"label": _("Value"),
			"value": flt(sum(flt(row.amount) for row in rows), 2),
			"indicator": "Green",
			"datatype": "Currency",
			"currency": currency,
		},
	]


def get_currency(company: str) -> str | None:
	return frappe.get_cached_value("Company", company, "default_currency")
