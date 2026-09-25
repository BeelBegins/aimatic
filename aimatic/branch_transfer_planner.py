"""Explainable branch-to-branch rebalancing suggestions.

Read-only: nothing here creates a Stock Entry or Material Request.  A branch is
a *receiver* for an Item when it sells the Item but holds less than the target
days of cover; another branch is a *donor* when it holds more than the days of
cover it must keep for itself.  Suggested moves are greedy, most urgent
receiver first, and never take a donor below its kept cover.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import frappe
from frappe.utils import add_days, flt, getdate, today

DEFAULT_HISTORY_DAYS = 28
MAX_HISTORY_DAYS = 90
DEFAULT_TARGET_COVER_DAYS = 7
DEFAULT_DONOR_KEEP_DAYS = 30
URGENT_DAYS_LEFT = 3
# Every retail Item here is stocked in Pcs with must_be_whole_number unset, so
# that flag alone would suggest moving half a piece.  Only weighed/measured
# UOMs may be moved in fractions; everything else moves in whole units.
FRACTIONAL_UOMS = {"Kg", "Gram", "Litre", "Ml", "Meter", "Cm", "Foot", "Inch"}


def plan_transfers(
	positions: list[dict[str, Any]],
	target_cover_days: int = DEFAULT_TARGET_COVER_DAYS,
	donor_keep_days: int = DEFAULT_DONOR_KEEP_DAYS,
) -> list[dict[str, Any]]:
	"""Turn per-Item, per-branch stock and sales into suggested moves.

	`positions` rows carry item_code, branch, stock_qty, sales_qty,
	history_days, whole_number and optional item/valuation context.  Negative
	stock is treated as zero: it is a stock-check problem, not a donor or a
	reliable measure of need.
	"""
	by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
	for raw in positions:
		row = dict(raw)
		history_days = max(int(row.get("history_days") or 1), 1)
		row["stock_qty"] = max(flt(row.get("stock_qty")), 0)
		row["daily_demand"] = max(flt(row.get("sales_qty")), 0) / history_days
		by_item[row["item_code"]].append(row)

	suggestions: list[dict[str, Any]] = []
	for item_rows in by_item.values():
		receivers = []
		for row in item_rows:
			demand = row["daily_demand"]
			if demand <= 0:
				continue
			days_left = row["stock_qty"] / demand
			need = demand * target_cover_days - row["stock_qty"]
			if days_left < target_cover_days and need > 0:
				receivers.append({**row, "days_left": days_left, "need": need})
		if not receivers:
			continue

		donors = []
		for row in item_rows:
			surplus = row["stock_qty"] - row["daily_demand"] * donor_keep_days
			if surplus > 0:
				donors.append({**row, "surplus": surplus})
		if not donors:
			continue

		receivers.sort(key=lambda r: (r["days_left"], -r["daily_demand"]))
		for receiver in receivers:
			remaining_need = receiver["need"]
			for donor in sorted(donors, key=lambda d: -d["surplus"]):
				if donor["branch"] == receiver["branch"] or remaining_need <= 0 or donor["surplus"] <= 0:
					continue
				move = min(remaining_need, donor["surplus"])
				if receiver.get("whole_number"):
					move = math.floor(move)
				if move <= 0:
					continue
				donor["surplus"] -= move
				remaining_need -= move
				suggestions.append(_suggestion(receiver, donor, move))

	suggestions.sort(key=lambda s: (s["priority_rank"], s["receiver_days_left"], -s["receiver_daily_demand"]))
	return suggestions


def _suggestion(receiver: dict[str, Any], donor: dict[str, Any], move: float) -> dict[str, Any]:
	receiver_stock = receiver["stock_qty"]
	if receiver_stock <= 0:
		priority = "Stock-out"
	elif receiver["days_left"] <= URGENT_DAYS_LEFT:
		priority = "Urgent"
	else:
		priority = "Top up"
	donor_demand = donor["daily_demand"]
	return {
		"item_code": receiver["item_code"],
		"item_name": receiver.get("item_name"),
		"item_group": receiver.get("item_group"),
		"stock_uom": receiver.get("stock_uom"),
		"priority": priority,
		"priority_rank": {"Stock-out": 0, "Urgent": 1, "Top up": 2}[priority],
		"receiver_branch": receiver["branch"],
		"receiver_stock": round(receiver_stock, 3),
		"receiver_daily_demand": round(receiver["daily_demand"], 3),
		"receiver_days_left": round(receiver["days_left"], 1),
		"receiver_need": round(receiver["need"], 3),
		"donor_branch": donor["branch"],
		"donor_stock": round(donor["stock_qty"], 3),
		"donor_daily_demand": round(donor_demand, 3),
		"donor_days_cover": round(donor["stock_qty"] / donor_demand, 1) if donor_demand else None,
		"suggested_qty": round(move, 3),
		"est_value": round(move * flt(donor.get("valuation_rate")), 2),
	}


def get_positions(company: str, history_days: int, item_group: str | None = None) -> list[dict[str, Any]]:
	"""Per Item and branch: stock on hand (Bin) and POS sales over the window.

	Only Items that sold somewhere in the window are returned - an Item nobody
	sells has no demand to serve.  A branch that sells an Item but has no Bin
	row for it is a genuine zero-stock position, so both sources are merged.
	"""
	date_from = add_days(getdate(today()), -(history_days - 1))
	params: dict[str, Any] = {
		"company": company,
		"date_from": date_from,
		"date_to": getdate(today()),
		"history_days": history_days,
	}
	item_group_clause = ""
	if item_group:
		item_group_clause = "AND item.`item_group` = %(item_group)s"
		params["item_group"] = item_group

	# nosemgrep
	sales = frappe.db.sql(
		f"""
		SELECT pii.`item_code`, pi.`branch`, SUM(pii.`stock_qty`) AS sales_qty
		FROM `tabPOS Invoice Item` pii
		INNER JOIN `tabPOS Invoice` pi ON pi.`name` = pii.`parent`
		INNER JOIN `tabItem` item ON item.`name` = pii.`item_code`
		WHERE pi.`docstatus` = 1 AND IFNULL(pi.`is_return`, 0) = 0
		  AND pi.`company` = %(company)s AND IFNULL(pi.`branch`, '') != ''
		  AND pi.`posting_date` BETWEEN %(date_from)s AND %(date_to)s
		  AND item.`is_stock_item` = 1 AND item.`disabled` = 0 {item_group_clause}
		GROUP BY pii.`item_code`, pi.`branch`
		""",
		params,
		as_dict=True,
	)
	if not sales:
		return []

	params["items"] = list({row.item_code for row in sales})
	# nosemgrep
	bins = frappe.db.sql(
		"""
		SELECT b.`item_code`, w.`custom_branch` AS branch,
		       SUM(b.`actual_qty`) AS stock_qty,
		       SUM(b.`stock_value`) AS stock_value
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.`name` = b.`warehouse`
		WHERE w.`company` = %(company)s AND w.`disabled` = 0 AND w.`is_group` = 0
		  AND IFNULL(w.`custom_branch`, '') != ''
		  AND b.`item_code` IN %(items)s
		GROUP BY b.`item_code`, w.`custom_branch`
		""",
		params,
		as_dict=True,
	)
	items = {
		row.name: row
		for row in frappe.db.sql(
			"""
			SELECT item.`name`, item.`item_name`, item.`item_group`, item.`stock_uom`,
			       IFNULL(uom.`must_be_whole_number`, 0) AS whole_number
			FROM `tabItem` item
			LEFT JOIN `tabUOM` uom ON uom.`name` = item.`stock_uom`
			WHERE item.`name` IN %(items)s
			""",
			params,
			as_dict=True,
		)
	}

	merged: dict[tuple[str, str], dict[str, Any]] = {}
	for row in bins:
		merged[(row.item_code, row.branch)] = {
			"item_code": row.item_code,
			"branch": row.branch,
			"stock_qty": flt(row.stock_qty),
			"valuation_rate": flt(row.stock_value) / flt(row.stock_qty) if flt(row.stock_qty) > 0 else 0,
			"sales_qty": 0,
		}
	for row in sales:
		key = (row.item_code, row.branch)
		position = merged.setdefault(
			key,
			{"item_code": row.item_code, "branch": row.branch, "stock_qty": 0, "valuation_rate": 0, "sales_qty": 0},
		)
		position["sales_qty"] = flt(row.sales_qty)

	positions = []
	for position in merged.values():
		meta = items.get(position["item_code"])
		if not meta:
			continue
		position.update(
			item_name=meta.item_name,
			item_group=meta.item_group,
			stock_uom=meta.stock_uom,
			whole_number=bool(meta.whole_number) or meta.stock_uom not in FRACTIONAL_UOMS,
			history_days=history_days,
		)
		positions.append(position)
	return positions
