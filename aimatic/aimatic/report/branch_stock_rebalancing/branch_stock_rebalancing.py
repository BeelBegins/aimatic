from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from aimatic.branch_transfer_planner import (
	DEFAULT_DONOR_KEEP_DAYS,
	DEFAULT_HISTORY_DAYS,
	DEFAULT_TARGET_COVER_DAYS,
	MAX_HISTORY_DAYS,
	get_positions,
	plan_transfers,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))

	history_days = min(max(cint(filters.history_days) or DEFAULT_HISTORY_DAYS, 1), MAX_HISTORY_DAYS)
	target_cover_days = max(cint(filters.target_cover_days) or DEFAULT_TARGET_COVER_DAYS, 1)
	donor_keep_days = max(cint(filters.donor_keep_days) or DEFAULT_DONOR_KEEP_DAYS, 1)

	positions = get_positions(filters.company, history_days, filters.get("item_group"))
	rows = plan_transfers(positions, target_cover_days, donor_keep_days)

	for fieldname in ("receiver_branch", "donor_branch"):
		if filters.get(fieldname):
			rows = [row for row in rows if row[fieldname] == filters[fieldname]]

	permitted = _permitted_branches()
	if permitted is not None:
		rows = [row for row in rows if row["receiver_branch"] in permitted or row["donor_branch"] in permitted]

	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	for row in rows:
		row["currency"] = currency

	message = _(
		"POS sales of the last {0} days set demand. A branch is short when it holds under {1} days of cover; "
		"a donor keeps {2} days of its own sales and offers the rest. Suggestions only - no stock document is created."
	).format(history_days, target_cover_days, donor_keep_days)
	return get_columns(), rows, message, None, get_report_summary(rows, currency)


def _permitted_branches() -> list[str] | None:
	branch_perms = frappe.permissions.get_user_permissions(frappe.session.user).get("Branch")
	if not branch_perms:
		return None
	return [p.doc for p in branch_perms]


def get_report_summary(rows, currency):
	return [
		{"label": _("Suggested Moves"), "value": len(rows), "indicator": "Blue", "datatype": "Int"},
		{"label": _("Items"), "value": len({r["item_code"] for r in rows}), "indicator": "Blue", "datatype": "Int"},
		{
			"label": _("Stock-outs Covered"),
			"value": len([r for r in rows if r["priority"] == "Stock-out"]),
			"indicator": "Red",
			"datatype": "Int",
		},
		{
			"label": _("Est. Value"),
			"value": round(sum(r["est_value"] for r in rows), 2),
			"indicator": "Green",
			"datatype": "Currency",
			"currency": currency,
		},
	]


def get_columns():
	def num(label, fieldname, width=90):
		return {"label": label, "fieldname": fieldname, "fieldtype": "Float", "width": width}

	def branch(label, fieldname):
		return {"label": label, "fieldname": fieldname, "fieldtype": "Link", "options": "Branch", "width": 160}

	return [
		{"label": _("Priority"), "fieldname": "priority", "fieldtype": "Data", "width": 80},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 190},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 120},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 60},
		branch(_("Move To"), "receiver_branch"),
		num(_("Stock There"), "receiver_stock"),
		num(_("Sales / Day There"), "receiver_daily_demand"),
		num(_("Days Left There"), "receiver_days_left"),
		num(_("Needs"), "receiver_need"),
		branch(_("Move From"), "donor_branch"),
		num(_("Stock Here"), "donor_stock"),
		num(_("Sales / Day Here"), "donor_daily_demand"),
		num(_("Days Cover Here"), "donor_days_cover"),
		num(_("Suggested Qty"), "suggested_qty", 110),
		{"label": _("Est. Value"), "fieldname": "est_value", "fieldtype": "Currency", "options": "currency", "width": 110},
	]
