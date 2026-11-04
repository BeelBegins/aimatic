"""Pure, reproducible calculations. Receipt cadence is never supplier lead time."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import Counter
from datetime import date
from statistics import median, pstdev
from urllib.parse import urlparse

VERSION = "retail-operations-v1"
CHANNELS = ("Inventory", "Accounts", "Sales", "Purchase")


def number(value):
	result = float(value or 0)
	return result if math.isfinite(result) else 0.0


def fingerprint(value):
	return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def webhook_valid(value):
	p = urlparse(value or "")
	bits = p.path.split("/")
	return (
		p.scheme == "https"
		and p.hostname in {"discord.com", "discordapp.com"}
		and not p.username
		and not p.password
		and p.port in (None, 443)
		and len(bits) == 5
		and bits[1:3] == ["api", "webhooks"]
		and bits[3].isdigit()
		and bool(bits[4])
		and not p.query
		and not p.fragment
	)


def cadence(receipts, default_days=7, cap_days=28):
	# Deduplicate partial receipts from the same PO; dates are actual receipts.
	events = {}
	for r in receipts:
		if number(r.get("qty")) <= 0:
			continue
		key = r.get("purchase_order") or r.get("receipt") or r["date"]
		events[key] = min(str(r["date"]), events.get(key, str(r["date"])))
	days = sorted({date.fromisoformat(d[:10]) for d in events.values()})
	gaps = [(b - a).days for a, b in itertools.pairwise(days) if (b - a).days > 0]
	if len(gaps) < 2:
		return {
			"days": min(default_days, cap_days),
			"basis": "Provisional category coverage",
			"observations": len(gaps),
			"exception_gaps": [],
			"lead_time_days": None,
		}
	centre = median(gaps)
	regular = [g for g in gaps if g <= max(centre * 2, 3)]
	value = min(max(1, math.ceil(median(regular))), cap_days)
	return {
		"days": value,
		"basis": "Observed receipt cadence; not supplier lead time",
		"observations": len(gaps),
		"exception_gaps": [g for g in gaps if g not in regular],
		"lead_time_days": None,
	}


def recommend(row, receipts, history_days=28, category_days=7):
	r = dict(row)
	daily = max(number(r.get("sales_qty")) / max(history_days, 1), 0)
	recent = max(number(r.get("recent_sales_qty")) / max(min(history_days, 7), 1), 0)
	# Avoid treating a single exceptional week as a persistent demand doubling.
	rate = max(daily, min(recent, daily * 1.5)) if daily else recent
	timing = cadence(
		receipts, category_days, category_days if category_days <= 2 else min(28, category_days * 3)
	)
	horizon = timing["days"]
	if r.get("calibrated_cover_days"):
		horizon = min(number(r["calibrated_cover_days"]), category_days if category_days <= 2 else 28)
		timing["basis"] = "Observed-sales walk-forward coverage replay; not supplier lead time"
	safety_days = min(max(horizon * 0.25, 1), 3)
	demand_sd = number(r.get("daily_sd"))
	safety = max(rate * safety_days, min(demand_sd * math.sqrt(horizon), rate * horizon))
	threshold = rate * max(1, horizon / 2) + safety
	target = rate * horizon + safety
	if category_days <= 2:
		target = min(target, rate * category_days)
		threshold = min(threshold, target)
	stock = number(r.get("actual_qty"))
	pending = number(r.get("pending_pos_qty"))
	physical = stock - pending - max(number(r.get("reserved_qty")), 0)
	available = physical - max(number(r.get("outgoing_transfer_qty")), 0)
	flags = list(r.get("flags") or [])
	if stock < 0 or physical < 0:
		flags.append("Negative or oversold stock; verify stock/reference before quantity automation")
	if r.get("disabled"):
		flags.append("Disabled item")
	if not r.get("supplier") and not r.get("source_warehouse"):
		flags.append("No supported purchasing source")
	incoming = max(number(r.get("open_supply_qty")), 0)
	unknown_supply = max(number(r.get("unknown_supply_qty")), 0)
	if unknown_supply:
		flags.append("Existing placed/open order has unknown or overdue arrival; review before another order")
	requested = max(number(r.get("requested_qty")), 0)
	eligible = available + incoming
	shortage = (rate > 0 or number(r.get("outgoing_transfer_qty")) > 0) and available <= threshold
	raw_qty = max(target - eligible, 0) if shortage else 0
	qty = math.ceil(raw_qty) if r.get("whole_units", True) else round(raw_qty, 3)
	# A pending requirement is not confirmed supply; it prevents a second request.
	if requested:
		flags.append("Outstanding Material Request: review or fulfil existing requirement")
	if r.get("source_warehouse") and not r.get("route_reviewed"):
		flags.append("Transfer allocation requires reviewed source and donor capacity")
	if stock < 0 or physical < 0:
		kind = "Negative stock"
	elif rate <= 0 and number(r.get("outgoing_transfer_qty")) <= 0:
		kind = "Slow moving" if stock > 0 else "No demand"
	elif available <= 0:
		kind = "Stockout risk"
	elif available <= threshold:
		kind = "Low cover"
	elif available > target * 2:
		kind = "Overstock"
	else:
		kind = "Healthy"
	r.update(
		daily_demand=round(rate, 4),
		available_qty=round(available, 3),
		days_cover=round(max(available, 0) / rate, 2) if rate else None,
		coverage_days=horizon,
		coverage_basis=timing["basis"],
		cadence_observations=timing["observations"],
		exception_gaps=timing["exception_gaps"],
		lead_time_days=None,
		reorder_level=round(threshold, 3),
		target_qty=round(target, 3),
		suggested_qty=qty,
		flags=list(dict.fromkeys(flags)),
		status=kind,
		confidence="Review"
		if flags
		else ("Observed pattern" if timing["observations"] >= 2 else "Provisional policy"),
		auto_eligible=not flags and qty > 0,
		estimated_value=round(qty * number(r.get("last_rate")), 2),
	)
	return r


def vendor_packs(rows):
	grouped = {}
	for r in rows:
		if (
			r.get("status") == "Negative stock"
			and number(r.get("daily_demand")) <= 0
			and number(r.get("outgoing_transfer_qty")) <= 0
		):
			continue
		if not r.get("suggested_qty") and r.get("status") not in (
			"Negative stock",
			"Stockout risk",
			"Low cover",
		):
			continue
		vendor = r.get("supplier") or (
			"Transfer: " + r["source_warehouse"] if r.get("source_warehouse") else "Unassigned source"
		)
		group = grouped.setdefault(
			vendor, {"vendor": vendor, "ready": [], "review": [], "estimated_value": 0}
		)
		group["review" if r.get("flags") else "ready"].append(r)
		if not r.get("flags"):
			group["estimated_value"] += number(r.get("estimated_value"))
	return sorted(grouped.values(), key=lambda g: g["vendor"])


def chunks(lines, limit=1800):
	result, current = [], ""
	for line in lines:
		for offset in range(0, max(len(line), 1), limit):
			piece = line[offset : offset + limit]
			if len(current) + len(piece) + 1 > limit:
				result.append(current)
				current = ""
			current += ("\n" if current else "") + piece
	if current:
		result.append(current)
	return result


def reference_candidates(rows, limit=100):
	"""Exact normalized full identity only; suggestions never approve a relationship."""
	import re

	groups = {}
	for r in rows:
		identity = re.sub(r"[^a-z0-9]", "", str(r.get("item_name", "")).lower())
		if len(identity) >= 6:
			groups.setdefault((identity, r.get("brand"), r.get("stock_uom")), []).append(r)
	matches = []
	for members in groups.values():
		if (
			len(members) > 1
			and any(number(r.get("actual_qty")) < 0 for r in members)
			and any(number(r.get("actual_qty")) > 0 for r in members)
		):
			matches.append(
				{
					"items": [r["item_code"] for r in members],
					"reason": "Matching normalized full name/brand/unit with split balances; identity and stock require review",
				}
			)
	return matches[:limit]


def replay_coverage(receipts, daily_sales, cap_days=28):
	"""Walk forward over regular receipt cycles using prior sales only.

	This compares cover against observed sales, not unknowable lost demand or
	procurement lead time. Historical late/exceptional gaps are excluded.
	"""
	from datetime import timedelta

	events = {}
	for r in receipts:
		if number(r.get("qty")) > 0:
			key = r.get("purchase_order") or r.get("receipt") or str(r["date"])
			day = date.fromisoformat(str(r["date"])[:10])
			events[key] = min(events.get(key, day), day)
	dates = sorted(set(events.values()))
	if len(dates) < 4:
		return {"status": "Insufficient regular cycles", "cycles": 0, "selected_days": None}
	observed = {date.fromisoformat(str(r["date"])[:10]): max(number(r["qty"]), 0) for r in daily_sales}
	if not observed:
		return {"status": "No observed sales", "cycles": 0, "selected_days": None}
	first = min(observed)
	gaps = [(b - a).days for a, b in itertools.pairwise(dates)]
	regular_limit = max(median(gaps) * 2, 3)
	candidates = sorted({min(d, cap_days) for d in (1, 3, 7, 14, 21, 28)})
	scores = {d: {"shortfall": 0.0, "average_excess": 0.0, "cycles": 0} for d in candidates}
	for start, end in itertools.pairwise(dates):
		length = (end - start).days
		prior_days = min((start - first).days, 28)
		if length > regular_limit or prior_days < 7:
			continue
		rate = sum(observed.get(start - timedelta(days=n), 0) for n in range(1, prior_days + 1)) / prior_days
		if rate <= 0:
			continue
		for horizon in candidates:
			initial = rate * horizon
			remaining = initial
			short = 0
			excess = 0
			for n in range(length):
				demand = observed.get(start + timedelta(days=n), 0)
				short += max(demand - max(remaining, 0), 0)
				remaining = max(remaining - demand, 0)
				excess += remaining
			score = scores[horizon]
			score["shortfall"] += short / max(rate, 1)
			score["average_excess"] += excess / max(length, 1) / max(rate, 1)
			score["cycles"] += 1
	valid = {d: v for d, v in scores.items() if v["cycles"] >= 2}
	if not valid:
		return {"status": "Insufficient regular cycles", "cycles": 0, "selected_days": None}
	# Published policy: shortage days carry 5x the weight of excess-cover days.
	selected = min(valid, key=lambda d: (5 * valid[d]["shortfall"] + valid[d]["average_excess"], d))
	return {
		"status": "Observed-sales walk-forward replay",
		"cycles": valid[selected]["cycles"],
		"selected_days": selected,
		"objective": "5 x observed shortage-days + average excess-cover-days; no lost-sales estimate",
		"candidates": {
			d: {k: round(v, 3) if isinstance(v, float) else v for k, v in value.items()}
			for d, value in valid.items()
		},
	}
