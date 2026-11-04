"""Donor protection and explicit transfer allocation, in stock units."""

from __future__ import annotations

import math
from collections import defaultdict

import frappe
from frappe.utils import add_days, flt, getdate, today


def capacity(warehouse, company):
	wh = frappe.db.get_value(
		"Warehouse", warehouse, ["company", "disabled", "is_group", "custom_branch"], as_dict=True
	)
	if not wh or wh.company != company or wh.disabled or wh.is_group or not wh.custom_branch:
		return {}
	p = {
		"warehouse": warehouse,
		"company": company,
		"start": add_days(today(), -28),
		"end": add_days(today(), -1),
	}
	stock = frappe.db.sql(
		"SELECT item_code,actual_qty,reserved_qty FROM tabBin WHERE warehouse=%(warehouse)s", p, as_dict=True
	)
	sales = frappe.db.sql(
		"""SELECT pii.item_code,SUM(pii.stock_qty) qty FROM `tabPOS Invoice Item` pii
        JOIN `tabPOS Invoice` pi ON pi.name=pii.parent WHERE pi.docstatus=1 AND pi.is_return=0
        AND pi.company=%(company)s AND pii.warehouse=%(warehouse)s
        AND pi.posting_date BETWEEN %(start)s AND %(end)s GROUP BY pii.item_code""",
		p,
		as_dict=True,
	)
	pending = frappe.db.sql(
		"""SELECT pii.item_code,SUM(pii.stock_qty) qty FROM `tabPOS Invoice Item` pii
        JOIN `tabPOS Invoice` pi ON pi.name=pii.parent WHERE pi.docstatus=1
        AND pi.company=%(company)s AND pii.warehouse=%(warehouse)s AND IFNULL(pi.consolidated_invoice,'')=''
        GROUP BY pii.item_code""",
		p,
		as_dict=True,
	)
	outgoing = frappe.db.sql(
		"""SELECT mri.item_code,SUM(GREATEST(mri.stock_qty-mri.ordered_qty,0)) qty
        FROM `tabMaterial Request Item` mri JOIN `tabMaterial Request` mr ON mr.name=mri.parent
        WHERE mr.docstatus=1 AND mr.company=%(company)s AND mr.material_request_type='Material Transfer'
        AND mri.from_warehouse=%(warehouse)s AND mr.status NOT IN ('Stopped','Cancelled','Transferred') GROUP BY mri.item_code""",
		p,
		as_dict=True,
	)
	demand = {r.item_code: flt(r.qty) / 28 for r in sales}
	waiting = {r.item_code: flt(r.qty) for r in pending}
	committed = {r.item_code: flt(r.qty) for r in outgoing}
	return {
		r.item_code: max(
			flt(r.actual_qty)
			- flt(r.reserved_qty)
			- waiting.get(r.item_code, 0)
			- committed.get(r.item_code, 0)
			- demand.get(r.item_code, 0) * 3,
			0,
		)
		for r in stock
	}


def allocate(rows, company):
	by_source = defaultdict(list)
	for row in rows:
		if row.get("source_warehouse"):
			by_source[row["source_warehouse"]].append(row)
	for warehouse, members in by_source.items():
		available = capacity(warehouse, company)
		for row in sorted(
			members, key=lambda r: r.get("days_cover") if r.get("days_cover") is not None else 1e9
		):
			if not row.get("route_reviewed"):
				continue
			limit = available.get(row["item_code"], 0)
			approved = min(float(row["suggested_qty"]), limit)
			approved = math.floor(approved) if row.get("whole_units") else round(approved, 3)
			if approved < float(row["suggested_qty"]):
				row["uncovered_qty"] = round(float(row["suggested_qty"]) - approved, 3)
				row["flags"].append(
					"Donor capacity does not cover the full shortage; remaining need requires a source decision"
				)
				row["auto_eligible"] = False
			row["transfer_available_qty"] = max(approved, 0)
			available[row["item_code"]] = max(limit - approved, 0)
