"""Small, explainable replenishment calculations for the Inventory Specialist.

This module deliberately never creates Purchase Orders, Material Requests, or
Stock Entries.  It turns current stock and submitted POS sales into a compact
buyer review list only.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today

CALCULATION_VERSION = "inventory-specialist-v1"
DEFAULT_HISTORY_DAYS = 28
MAX_HISTORY_DAYS = 90
MAX_ROWS = 500


def calculate_recommendations(
	positions: list[dict[str, Any]], target_cover_days: int = 7
) -> list[dict[str, Any]]:
	"""Return only explainable purchasing rows from already-scoped data.

	Rows without a single configured external supplier, sales history, or a
	non-negative stock balance never enter the purchasing list.  They belong in
	the stock-check report where a buyer can resolve them without contaminating
	the order suggestion.
	"""
	target_cover_days = max(1, cint(target_cover_days or 7))
	rows = []
	for raw in positions:
		row = dict(raw)
		stock_qty = flt(row.get("stock_qty"))
		daily_demand = max(flt(row.get("sales_qty")) / max(cint(row.get("history_days")), 1), 0)
		row["daily_demand"] = round(daily_demand, 4)
		row["target_cover_days"] = target_cover_days
		row["target_qty"] = round(daily_demand * target_cover_days, 4)
		row["days_left"] = round(stock_qty / daily_demand, 1) if daily_demand else None

		if stock_qty < 0:
			row["review_reason"] = "Negative stock needs a stock check."
			continue
		if not row.get("supplier"):
			row["review_reason"] = "No single external supplier is configured."
			continue
		if daily_demand <= 0:
			row["review_reason"] = "No recent retail sales."
			continue

		row["suggested_qty"] = round(max(row["target_qty"] - stock_qty, 0), 4)
		if row["suggested_qty"] <= 0:
			continue
		row["action"] = "Order now" if row["days_left"] <= 3 else "Top up"
		row["reason"] = f"Restore {target_cover_days} days of sales cover."
		rows.append(row)

	rows.sort(key=lambda r: (0 if r["action"] == "Order now" else 1, r["days_left"], -r["daily_demand"]))
	return rows


def calculate_lead_time_days(delivery_days: list[float]) -> dict[str, Any]:
	"""Use only genuine positive PO-to-receipt intervals; never invent a lead time."""
	valid_days = sorted(flt(days) for days in delivery_days if flt(days) > 0)
	if not valid_days:
		return {"lead_time_days": None, "observations": 0, "status": "Learning"}
	return {
		"lead_time_days": round(float(median(valid_days)), 1),
		"observations": len(valid_days),
		"status": "Observed",
	}


def _company(company: str | None) -> str:
	company = company or frappe.defaults.get_user_default("Company") or frappe.defaults.get_default("company")
	if not company:
		frappe.throw(_("Company is required."))
	return company


def _check_context(company: str, branch: str):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	if not branch:
		frappe.throw(_("Branch is required."))
	if not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)
	if frappe.db.get_value("Branch", branch, "company") != company:
		frappe.throw(_("Branch does not belong to the selected company."))


def _positions(company: str, branch: str, history_days: int) -> list[dict[str, Any]]:
	date_from = add_days(getdate(today()), -(history_days - 1))
	# The latest submitted Purchase Receipt for this branch is the primary
	# supply source. That includes sister-branch purchases without manually
	# mapping thousands of Items. Item master mapping remains a fallback when
	# a receipt history is not available.
	return frappe.db.sql(
		"""
		SELECT b.item_code, MAX(i.item_name) AS item_name,
		       SUM(b.actual_qty) AS stock_qty,
		       COALESCE(s.sales_qty, 0) AS sales_qty,
		       COALESCE(purchase_history.supplier, preferred.supplier, single_supplier.supplier) AS supplier,
		       %(history_days)s AS history_days
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		INNER JOIN `tabItem` i ON i.name = b.item_code
		LEFT JOIN (
			SELECT idef.parent AS item_code, MAX(idef.default_supplier) AS supplier
			FROM `tabItem Default` idef
			WHERE (idef.company = %(company)s OR IFNULL(idef.company, '') = '')
			  AND IFNULL(idef.default_supplier, '') != ''
			GROUP BY idef.parent
		) preferred ON preferred.item_code = b.item_code
		LEFT JOIN (
			SELECT isup.parent AS item_code, MAX(isup.supplier) AS supplier
			FROM `tabItem Supplier` isup
			WHERE IFNULL(isup.supplier, '') != ''
			GROUP BY isup.parent
			HAVING COUNT(DISTINCT isup.supplier) = 1
		) single_supplier ON single_supplier.item_code = b.item_code
		LEFT JOIN (
			SELECT item_code, supplier
			FROM (
				SELECT pri.item_code, pr.supplier,
				       ROW_NUMBER() OVER (
					       PARTITION BY pri.item_code
					       ORDER BY pr.posting_date DESC, pr.modified DESC, pr.name DESC, pri.idx DESC
				       ) AS row_num
				FROM `tabPurchase Receipt Item` pri
				INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
				WHERE pr.docstatus = 1 AND IFNULL(pr.is_return, 0) = 0
				  AND pr.company = %(company)s AND pr.branch = %(branch)s
				  AND IFNULL(pr.supplier, '') != ''
			) latest_purchase
			WHERE row_num = 1
		) purchase_history ON purchase_history.item_code = b.item_code
		LEFT JOIN (
			SELECT pii.item_code, SUM(pii.stock_qty) AS sales_qty
			FROM `tabPOS Invoice Item` pii
			INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
			WHERE pi.docstatus = 1 AND IFNULL(pi.is_return, 0) = 0
			  AND pi.company = %(company)s AND pi.branch = %(branch)s
			  AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
			GROUP BY pii.item_code
		) s ON s.item_code = b.item_code
		WHERE w.company = %(company)s AND w.custom_branch = %(branch)s
		  AND w.disabled = 0 AND i.disabled = 0
		GROUP BY b.item_code, s.sales_qty, purchase_history.supplier, preferred.supplier, single_supplier.supplier
		""",
		{"company": company, "branch": branch, "date_from": date_from, "date_to": getdate(today()), "history_days": history_days},
		as_dict=True,
	)


def _stock_checks(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
	checks = []
	for row in positions:
		stock_qty = flt(row.stock_qty)
		sales_qty = flt(row.sales_qty)
		if stock_qty < 0:
			reason = "Negative stock"
		elif sales_qty > 0 and stock_qty == 0:
			reason = "Previously sold, now out of stock"
		elif sales_qty > 0 and not row.supplier:
			reason = "Selling item has no single external supplier"
		else:
			continue
		checks.append({"item_code": row.item_code, "item_name": row.item_name, "stock_qty": stock_qty, "sales_qty": sales_qty, "reason": reason})
	checks.sort(key=lambda r: (0 if r["reason"] == "Negative stock" else 1, r["stock_qty"]))
	return checks[:MAX_ROWS]


def _supplier_timing(company: str, branch: str) -> list[dict[str, Any]]:
	rows = frappe.db.sql(
		"""
		SELECT pr.supplier, po.transaction_date AS ordered_on, pr.posting_date AS received_on
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		INNER JOIN `tabPurchase Order` po ON po.name = pri.purchase_order
		WHERE pr.docstatus = 1 AND po.docstatus = 1
		  AND IFNULL(pr.is_return, 0) = 0 AND pr.company = %(company)s
		  AND pr.branch = %(branch)s AND pr.posting_date >= %(date_from)s
		  AND pr.posting_date > po.transaction_date
		""",
		{"company": company, "branch": branch, "date_from": add_days(getdate(today()), -365)},
		as_dict=True,
	)
	by_supplier = defaultdict(list)
	for row in rows:
		by_supplier[row.supplier].append(date_diff(row.received_on, row.ordered_on))
	return [dict({"supplier": supplier}, **calculate_lead_time_days(days)) for supplier, days in sorted(by_supplier.items())]


@frappe.whitelist()
def get_inventory_specialist(branch: str, company: str | None = None, history_days: int = DEFAULT_HISTORY_DAYS, target_cover_days: int = 7, supplier: str | None = None):
	"""Return all read-only panels for the Inventory Specialist Desk page."""
	company = _company(company)
	_check_context(company, branch)
	history_days = max(14, min(cint(history_days or DEFAULT_HISTORY_DAYS), MAX_HISTORY_DAYS))
	target_cover_days = max(1, min(cint(target_cover_days or 7), 30))
	positions = _positions(company, branch, history_days)
	recommendations = [row for row in calculate_recommendations(positions, target_cover_days) if not supplier or row["supplier"] == supplier][:MAX_ROWS]
	by_supplier = defaultdict(list)
	for row in recommendations:
		by_supplier[row["supplier"]].append(row)
	return {
		"company": company,
		"branch": branch,
		"history_days": history_days,
		"target_cover_days": target_cover_days,
		"recommendations": recommendations,
		"supplier_groups": [{"supplier": supplier, "items": items, "item_count": len(items)} for supplier, items in sorted(by_supplier.items())],
		"stock_checks": _stock_checks(positions),
		"supplier_timing": _supplier_timing(company, branch),
		"calculation_version": CALCULATION_VERSION,
		"read_only": True,
	}
