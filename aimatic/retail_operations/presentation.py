"""Compact Discord report cards. No queries, credentials or purchasing decisions."""

from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlencode

COLORS = {"Inventory": 0xD97706, "Sales": 0x2563EB, "Accounts": 0x7C3AED, "Purchase": 0x0F766E}


def text(value, limit=100):
	value = re.sub(r"\s+", " ", str(value or "—")).strip()
	value = re.sub(r"([\\`*_~|<>\[\]])", r"\\\1", value)
	return value if len(value) <= limit else value[: limit - 1] + "…"


def number(value, decimals=2):
	if value is None:
		return "—"
	return f"{float(value):,.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{float(value):,.0f}"


def link(base, channel, branch=None):
	return base + "?" + urlencode(dict(channel=channel, **({"branch": branch} if branch else {})))


def card(title, channel, description, url):
	return dict(
		title=text(title, 240), color=COLORS[channel], description=description[:3500], url=url, fields=[]
	)


def field(embed, name, value, inline=False):
	# Reserve room for title, description and footer beneath Discord's 6000 total limit.
	used = (
		len(embed["title"])
		+ len(embed["description"])
		+ sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
	)
	budget = min(1000, 5500 - used - len(name))
	if budget > 40 and len(embed["fields"]) < 24:
		value = value or "None recorded"
		if len(value) > budget:
			# Keep complete item blocks instead of severing a quantity or a link.
			blocks = value.split("\n\n")
			kept = []
			for block in blocks:
				if len("\n\n".join(kept + [block])) > budget - 60:
					break
				kept.append(block)
			value = "\n\n".join(kept + ["Additional detail in the full report linked in this card."])
		embed["fields"].append(dict(name=name, value=value, inline=inline))


def payload(embed, page, total):
	return dict(
		content="",
		embeds=[dict(embed, footer={"text": f"SZL Operations · {page}/{total}"})],
		allowed_mentions={"parse": []},
	)


def item(row):
	return f"**{text(row.get('item_name') or row.get('item_code'), 65)}** · {text(row.get('item_code'), 28)}"


def inventory(snap, base):
	r = snap["reports"]["Inventory"]
	e = card(
		snap["branch"],
		"Inventory",
		"Review selling-stock risks first; check negative balances against physical stock.",
		link(base, "Inventory", snap["branch"]),
	)
	for status, label in (
		("Stockout risk", "Stockout risk"),
		("Low cover", "Low stock"),
		("Overstock", "Overstock"),
		("Negative stock", "Negative balances"),
		("Slow moving", "Slow moving"),
		("Healthy", "Healthy"),
	):
		field(e, label, number(r["summary"].get(status, 0), 0), True)
	for statuses, label, count in (
		({"Stockout risk", "Low cover"}, "Replenishment priorities · top 4", 4),
		({"Negative stock"}, "Verify stock / item references · top 2", 2),
		({"Overstock"}, "Reduce buying · top 2", 2),
	):
		rows = [row for row in r["rows"] if row["status"] in statuses]
		rows.sort(
			key=lambda row: (
				(
					row.get("status") != "Stockout risk",
					row.get("days_cover") if row.get("days_cover") is not None else 1e9,
				)
				if "Low cover" in statuses
				else (0, 0),
				-float(row.get("daily_demand") or 0),
				str(row.get("item_code")),
			)
		)
		out = [
			item(row)
			+ f"\nStock {number(row.get('actual_qty'))} · cover {number(row.get('days_cover'))} days · selling {number(row.get('daily_demand'))}/day"
			for row in rows[:count]
		]
		if len(rows) > count:
			out.append(f"+{len(rows) - count:,} more in the full report")
		field(e, label, "\n\n".join(out) or "None recorded")
	return e


def sales(snap, base):
	r = snap["reports"]["Sales"]
	e = card(
		snap["branch"],
		"Sales",
		f"Business date: **{r['date']}** · amounts in company currency",
		link(base, "Sales", snap["branch"]),
	)
	field(e, "Sales before refunds", number(r["gross_sales"]), True)
	field(e, "Refund value", number(r["total_refunds"]), True)
	field(e, "Refund transactions", number(r["refund_count"], 0), True)
	field(
		e,
		"Highest refund activity · top 3 counters",
		"\n".join(
			f"**{text(c['pos_profile'], 65)}** — {number(c['refunds'])} · {number(c['refund_activity_percent'])}% of sales"
			for c in r.get("highest_refund_counters", [])[:3]
		)
		or "No eligible counter comparison. Minimum receipt count applies.",
	)
	field(
		e,
		f"Large refunds · threshold {number(r['large_refund_threshold'])}",
		"\n".join(
			f"**{number(row['amount'])}** · {text(row['pos_profile'], 40)} · {text(row['name'], 45)}"
			for row in r["large_refunds"][:3]
		)
		or "None recorded",
	)
	for key, title in (
		("negative", "Negative-margin sales"),
		("lowest", "Lowest-margin items"),
		("highest", "Highest-margin items"),
	):
		field(
			e,
			title + " · top 2",
			"\n\n".join(
				item(row)
				+ f"\nMargin {number(row['margin_percent'])}% · sold {number(row['qty'])} · profit {number(row['profit'])}"
				for row in r["margins"].get(key, [])[:2]
			)
			or ("Unavailable" if r["margins"]["status"] == "Unavailable" else "None recorded"),
		)
	field(
		e,
		"Voids · recorded events only",
		"\n".join(
			f"{text(v['pos_profile'], 60)} · {text(v['event_type'], 30)}: {number(v['events'], 0)} · value {number(v['amount'])}"
			for v in sorted(r["voids"], key=lambda v: -v["events"])[:3]
		)
		or "Client capture is not connected. Complete void totals and counter rankings are unavailable.",
	)
	field(
		e,
		"Coverage",
		f"Margins: {r['margins']['status']} (recorded costs). Refund ratios show activity, not proven cashier error. Full lists and pending-cost details are in the report.",
	)
	return e


def balances(rows):
	by_currency = defaultdict(lambda: [0, 0])
	for row in rows:
		by_currency[row["currency"]][0] += float(row["outstanding"])
		by_currency[row["currency"]][1] += float(row["overdue"])
	return (
		"\n".join(
			f"{text(currency, 15)} **{number(total)}** outstanding · **{number(overdue)}** overdue"
			for currency, (total, overdue) in sorted(by_currency.items())
		)
		or "No outstanding supplier balances recorded"
	)


def accounts(snap, base):
	r = snap["reports"]["Accounts"]
	e = card(
		snap["branch"],
		"Accounts",
		"Current supplier balances · overdue relative to " + r["as_of"],
		link(base, "Accounts", snap["branch"]),
	)
	field(e, "Payables by currency", balances(r["supplier_dues"]))
	rows = sorted(r["supplier_dues"], key=lambda row: -abs(float(row["overdue"])))
	field(
		e,
		"Supplier balances · top 5 by overdue value",
		"\n\n".join(
			f"**{text(row['supplier'], 70)}**\n{text(row['currency'], 15)} {number(row['outstanding'])} due · {number(row['overdue'])} overdue · {number(row['bills'], 0)} bills"
			for row in rows[:5]
		)
		or "None recorded",
	)
	field(
		e,
		"Received but not fully billed · top 3",
		"\n\n".join(
			f"**{text(row['supplier'], 70)}**\nEstimated net {number(row['estimated_unbilled_net'])} · {number(row['receipts'], 0)} receipts"
			for row in sorted(
				r["received_unbilled"], key=lambda row: -abs(float(row["estimated_unbilled_net"]))
			)[:3]
		)
		or "None recorded",
	)
	field(
		e,
		"Cash reconciliation",
		"Unavailable — independent opening, closing and settlement evidence is needed.",
	)
	return e


def vendor_cards(snapshots, base):
	groups = defaultdict(list)
	for snap in snapshots:
		for vendor in snap["reports"].get("Purchase", {}).get("vendors", []):
			groups[vendor["vendor"]].append((snap, vendor))

	def priority(pair):
		name, packs = pair
		return (
			-sum(len(v["ready"]) for _, v in packs),
			-sum(float(row.get("daily_demand") or 0) for _, v in packs for row in v["review"]),
			name,
		)

	result = []
	for name, packs in sorted(groups.items(), key=priority):
		ready = sum(len(v["ready"]) for _, v in packs)
		review = sum(len(v["review"]) for _, v in packs)
		e = card(
			name,
			"Purchase",
			"**ORDER PROPOSAL** · verify orders already placed before buying.\n"
			+ (
				"Review-only lines below are held for verification."
				if review
				else "Quantities below are proposals for procurement review."
			),
			link(base, "Purchase", packs[0][0]["branch"]),
		)
		field(e, "Ready lines", number(ready, 0), True)
		field(e, "Review lines", number(review, 0), True)
		field(e, "Ready-line estimate", number(sum(v["estimated_value"] for _, v in packs)), True)
		e["description"] += "\nEvidence: " + min(s["cutoff"] for s, _ in packs)[:16] + " PKT"
		for snap, vendor in packs:
			rows = []
			for row in vendor["ready"][:3]:
				rows.append(
					item(row)
					+ f"\nOrder **{number(row['suggested_qty'])} {text(row['stock_uom'], 12)}** · cover {number(row['days_cover'])}d"
				)
			for row in vendor["review"][: 1 if rows else 2]:
				rows.append(
					"**HOLD — verify first**\n"
					+ item(row)
					+ "\n"
					+ text("; ".join(row.get("flags") or ["Stock evidence needs review"]), 150)
				)
			shown = min(3, len(vendor["ready"])) + min(1 if vendor["ready"] else 2, len(vendor["review"]))
			if len(vendor["ready"]) + len(vendor["review"]) > shown:
				rows.append("More lines in the full vendor report.")
			rows.append(f"[Full branch order]({link(base, 'Purchase', snap['branch'])})")
			field(
				e,
				f"{text(snap['branch'], 65)} · {len(vendor['ready'])} ready / {len(vendor['review'])} review",
				"\n\n".join(rows),
			)
		result.append(e)
	return result


def digest(snapshots, channel, base, max_pages=8):
	"""One whole branch/vendor per card, plus a summary; no arbitrary text splitting."""
	max_pages = max(1, int(max_pages))
	available = [s for s in snapshots if s.get("reports", {}).get(channel) is not None]
	available_names = {s["branch"] for s in available}
	missing = [s["branch"] for s in snapshots if s["branch"] not in available_names]
	e = card(
		channel + " | Branch overview",
		channel,
		"Branch reports and priorities below. Open a card title for the complete report.",
		link(base, channel),
	)
	if channel == "Purchase":
		cards = vendor_cards(available, base)
		field(
			e,
			"Procurement queue",
			f"**{len(cards):,} sources** across **{len(available)} branches**. Vendors with ready lines appear first; review-only sources follow by sales demand.",
		)
		for snap in available:
			vendors = snap["reports"][channel].get("vendors", [])
			field(
				e,
				text(snap["branch"], 80),
				f"{sum(len(v['ready']) for v in vendors):,} ready lines · {sum(len(v['review']) for v in vendors):,} held for review",
			)
		field(
			e,
			"Before placing an order",
			"Open the full vendor report; record orders already placed. Quantities use stock units. Estimates use historical purchase prices in company currency. Automatic Material Requests are off unless separately enabled.",
		)
	else:
		cards = [
			{"Inventory": inventory, "Sales": sales, "Accounts": accounts}[channel](s, base)
			for s in available
		]
		for embed, snap in zip(cards, available, strict=True):
			embed["description"] += "\nEvidence: " + snap["cutoff"][:16] + " PKT"
		for snap in available:
			r = snap["reports"][channel]
			if channel == "Inventory":
				s = r["summary"]
				value = f"{s.get('Stockout risk', 0):,} stockout risks · {s.get('Low cover', 0):,} low stock\n{s.get('Negative stock', 0):,} negative balances · {s.get('Overstock', 0):,} overstock"
			elif channel == "Sales":
				value = f"Sales **{number(r['gross_sales'])}** · refunds **{number(r['total_refunds'])}** ({number(r['refund_count'], 0)}) · {r['date']}"
			else:
				value = balances(r["supplier_dues"])
			field(e, text(snap["branch"], 80), value)
	if missing:
		field(e, "Reports unavailable — excluded from summaries", "\n".join(text(b, 100) for b in missing))
	if len(cards) > max_pages - 1:
		field(
			e,
			"Full report continues in ERPNext",
			f"Showing {max_pages - 1} of {len(cards)} {'source' if channel == 'Purchase' else 'branch'} cards. The full list is available in the linked Operations Console.",
		)
	cards = [e] + cards[: max_pages - 1]
	if channel in ("Inventory", "Accounts") and len(cards) < max_pages:
		note = card(
			channel + " | Reading this report",
			channel,
			"Use the branch cards above to act on the report.",
			link(base, channel),
		)
		if channel == "Inventory":
			field(
				note,
				"Low stock follows sales pace",
				"Cover is estimated from observed sales and replenishment history. A small quantity alone does not mean low stock. No confirmed supplier lead time is inferred.",
			)
			field(
				note,
				"Negative stock is a verification task",
				"Check physical stock and duplicate item references. Different brands stay separate. These balances have not been automatically corrected or netted.",
			)
		else:
			field(
				note,
				"Keep balances separate",
				"Invoice currencies are shown separately. Unbilled amounts are estimates in company currency, not posted supplier invoices. Company-only balances are not assigned to branches.",
			)
			field(
				note,
				"Full lists",
				"Branch cards show the largest recorded balances. Open the linked report for all supplier entries and supporting evidence.",
			)
		cards.append(note)
	for embed in cards:
		if "\nEvidence:" not in embed["description"]:
			embed["description"] += "\n" + (
				"Oldest evidence: " + min(s["cutoff"] for s in available)[:16] + " PKT"
				if available
				else "No fresh report evidence available."
			)
	return [payload(embed, i + 1, len(cards)) for i, embed in enumerate(cards)]


def urgent(snapshot, issue, evidence, base):
	e = card(
		"Action needed | " + issue["subject"],
		issue["channel"],
		"**"
		+ text(snapshot["branch"])
		+ "**\n"
		+ text(evidence.get("item_name") or evidence.get("name"), 180),
		link(base, issue["channel"], snapshot["branch"]),
	)
	e["color"] = 0xDC2626
	for key, title, unit in (
		("actual_qty", "Recorded stock", ""),
		("days_cover", "Estimated cover", " days"),
		("amount", "Refund amount", ""),
	):
		if evidence.get(key) is not None:
			field(e, title, number(evidence[key]) + unit, True)
	if evidence.get("flags"):
		field(e, "Check before acting", "\n".join("• " + text(flag, 200) for flag in evidence["flags"][:4]))
	return payload(e, 1, 1)
