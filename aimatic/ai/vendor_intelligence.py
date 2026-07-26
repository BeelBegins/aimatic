"""Certified vendor reliability metrics."""

from __future__ import annotations

import frappe
from frappe.utils import add_days, cint, flt, getdate

from aimatic.ai.tools import _resolve_company

CALCULATION_VERSION = "vendor-reliability-v1"


def get_vendor_reliability(
	supplier: str | None = None,
	history_days: int = 365,
	minimum_orders: int = 2,
	limit: int = 100,
) -> dict:
	company = _resolve_company()
	history_days = max(90, min(cint(history_days or 365), 1095))
	minimum_orders = max(1, min(cint(minimum_orders or 2), 50))
	limit = max(1, min(cint(limit or 100), 300))
	date_to = getdate()
	date_from = add_days(date_to, -history_days + 1)
	supplier_clause = "AND po.supplier = %(supplier)s" if supplier else ""
	rows = frappe.db.sql(
		f"""
		SELECT po.supplier, MAX(po.supplier_name) AS supplier_name,
		       COUNT(DISTINCT po.name) AS purchase_orders,
		       SUM(poi.base_amount) AS purchase_volume,
		       SUM(poi.qty) AS ordered_qty,
		       SUM(COALESCE(receipts.received_qty, 0)) AS received_qty,
		       SUM(COALESCE(receipts.rejected_qty, 0)) AS rejected_qty,
		       AVG(receipts.lead_time_days) AS average_lead_time_days,
		       STDDEV_POP(receipts.lead_time_days) AS lead_time_stddev,
		       SUM(CASE WHEN receipts.latest_receipt_date > poi.schedule_date THEN 1 ELSE 0 END) AS late_lines,
		       SUM(CASE WHEN COALESCE(receipts.received_qty, 0) < poi.qty THEN 1 ELSE 0 END) AS short_lines,
		       COUNT(poi.name) AS total_lines,
		       AVG(CASE WHEN po.transaction_date < DATE_SUB(%(date_to)s, INTERVAL %(half_days)s DAY) THEN poi.base_rate END) AS older_price,
		       AVG(CASE WHEN po.transaction_date >= DATE_SUB(%(date_to)s, INTERVAL %(half_days)s DAY) THEN poi.base_rate END) AS recent_price
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
		LEFT JOIN (
		  SELECT pri.purchase_order_item,
		         SUM(CASE WHEN pr.is_return = 0 THEN pri.qty ELSE -ABS(pri.qty) END) AS received_qty,
		         SUM(COALESCE(pri.rejected_qty, 0)) AS rejected_qty,
		         MAX(pr.posting_date) AS latest_receipt_date,
		         AVG(DATEDIFF(pr.posting_date, po2.transaction_date)) AS lead_time_days
		  FROM `tabPurchase Receipt Item` pri
		  INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		  INNER JOIN `tabPurchase Order` po2 ON po2.name = pri.purchase_order
		  WHERE pr.docstatus = 1 AND pr.company = %(company)s
		    AND pr.posting_date BETWEEN %(date_from)s AND %(date_to)s
		  GROUP BY pri.purchase_order_item
		) receipts ON receipts.purchase_order_item = poi.name
		WHERE po.docstatus = 1 AND po.company = %(company)s
		  AND po.transaction_date BETWEEN %(date_from)s AND %(date_to)s
		  {supplier_clause}
		GROUP BY po.supplier
		HAVING purchase_orders >= %(minimum_orders)s
		ORDER BY purchase_volume DESC
		LIMIT %(limit)s
		""",
		{
			"company": company,
			"supplier": supplier,
			"date_from": date_from,
			"date_to": date_to,
			"half_days": history_days // 2,
			"minimum_orders": minimum_orders,
			"limit": limit,
		},
		as_dict=True,
	)
	total_volume = sum(flt(row.purchase_volume) for row in rows)
	vendors = []
	for raw in rows:
		row = dict(raw)
		ordered = flt(row["ordered_qty"])
		received = flt(row["received_qty"])
		total_lines = max(cint(row["total_lines"]), 1)
		older_price = flt(row["older_price"])
		recent_price = flt(row["recent_price"])
		on_time_pct = max(0, (1 - cint(row["late_lines"]) / total_lines) * 100)
		fill_rate = received / ordered * 100 if ordered else None
		rejection_rate = flt(row["rejected_qty"]) / received * 100 if received else None
		price_trend = (recent_price - older_price) / older_price * 100 if older_price else None
		concentration = flt(row["purchase_volume"]) / total_volume * 100 if total_volume else 0
		reliability = (
			on_time_pct * 0.40
			+ min(flt(fill_rate), 100) * 0.35
			+ max(0, 100 - flt(rejection_rate)) * 0.15
			+ max(0, 100 - min(flt(row["lead_time_stddev"]) * 5, 100)) * 0.10
		)
		row.update(
			{
				"fill_rate_pct": round(fill_rate, 2) if fill_rate is not None else None,
				"on_time_delivery_pct": round(on_time_pct, 2),
				"short_supply_qty": round(max(ordered - received, 0), 4),
				"rejection_rate_pct": round(rejection_rate, 2) if rejection_rate is not None else None,
				"price_trend_pct": round(price_trend, 2) if price_trend is not None else None,
				"vendor_concentration_risk_pct": round(concentration, 2),
				"reliability_score": round(reliability, 2),
			}
		)
		vendors.append(row)
	return {
		"company": company,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"vendors": vendors,
		"row_count": len(vendors),
		"calculation_version": CALCULATION_VERSION,
	}


TOOL_SPECS = [{
	"type": "function",
	"function": {
		"name": "get_vendor_reliability",
		"description": "Certified vendor purchase volume, price trend, lead-time consistency, PO-versus-receipt variance, short supply, late delivery, rejection rate, concentration risk, and reliability score.",
		"parameters": {
			"type": "object",
			"properties": {
				"supplier": {"type": "string"},
				"history_days": {"type": "integer"},
				"minimum_orders": {"type": "integer"},
				"limit": {"type": "integer"},
			},
		},
	},
}]

TOOL_DISPATCH = {"get_vendor_reliability": get_vendor_reliability}
