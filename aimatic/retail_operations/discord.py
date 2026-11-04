"""Durable webhook delivery. Secrets never enter payloads, errors or AI context."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

import frappe
import requests
from frappe.utils import get_url, now_datetime

from aimatic.retail_operations.calculations import CHANNELS, chunks, fingerprint, webhook_valid
from aimatic.retail_operations.service import latest, settings, write_internal


def operations_url():
	return (settings().get("public_base_url") or get_url()).rstrip("/") + "/app/retail-operations-console"


def route(branch, channel):
	doc = frappe.get_doc("Branch", branch)
	value = doc.get_password("custom_discord_" + channel.lower(), raise_exception=False)
	return value if value and webhook_valid(value) else None


def lines(snapshot, channel):
	report = snapshot["reports"].get(channel)
	out = [f"**{snapshot['branch']} — {channel}**", f"Evidence: {snapshot['cutoff']} PKT"]
	out.append(operations_url() + "?branch=" + requests.utils.quote(snapshot["branch"]))
	if report is None:
		return out + ["Report unavailable. Check Operations Console; no zero result has been inferred."]
	if channel == "Inventory":
		out.append(" | ".join(f"{k}: {v}" for k, v in report["summary"].items()))
		for r in report["rows"][:20]:
			if r["status"] != "Healthy":
				out.append(
					f"{r['item_code']} {r['item_name']}: {r['status']}; recorded {r['actual_qty']}; cover {r['days_cover']} days; {r['confidence']}"
				)
		out.append("Complete inventory and reference exceptions are in the linked console.")
	elif channel == "Purchase":
		out.append(
			"PROPOSED ORDERS — quantities are stock units; values are estimates. Check unrecorded placed orders."
		)
		for vendor in report["vendors"]:
			out.append(
				f"**{vendor['vendor']}** — estimate {vendor['estimated_value']:.2f}; ready {len(vendor['ready'])}, review {len(vendor['review'])}"
			)
			for r in vendor["ready"][:15]:
				out.append(
					f"{r['item_name']} [{r['item_code']}]: propose {r['suggested_qty']} {r['stock_uom']}; demand {r['daily_demand']}/day; cover {r['days_cover']}d; last PR {r.get('last_receipt', '—')}"
				)
			for r in vendor["review"][:5]:
				out.append(f"REVIEW {r['item_code']}: {'; '.join(r['flags'])}")
			if len(vendor["ready"]) > 15 or len(vendor["review"]) > 5:
				out.append("Additional lines in Operations Console.")
	elif channel == "Sales":
		out.append(
			f"Business date {report['date']}; sales {report['gross_sales']:.2f}; refunds {report['total_refunds']:.2f} ({report['refund_count']})"
		)
		for r in report["counters"]:
			out.append(
				f"{r['pos_profile']}: {r['receipts']} receipts; refunds {r['refunds']}; activity ratio {r['refund_activity_percent']}%; pending close {r['pending_closing']}"
			)
		out.append("Refund ratios measure activity, not proven cashier error. " + report["void_coverage"])
		out.append("Margin status: " + report["margins"]["status"])
		for kind in ("negative", "lowest", "highest"):
			for r in report["margins"].get(kind, [])[:5]:
				out.append(
					f"{kind}: {r['item_name']} qty {r['qty']}; profit {r['profit']}; margin {r['margin_percent']}% (recorded cost)"
				)
		for r in report["large_refunds"][:10]:
			out.append(f"Large refund {r['name']}: {r['amount']} at {r['pos_profile']}")
		for r in report["voids"]:
			out.append(
				f"Recorded {r['event_type']} at {r['pos_profile']}: {r['events']}, value {r['amount']}"
			)
	else:
		out.append(report["note"])
		for r in report["supplier_dues"][:30]:
			out.append(
				f"{r['supplier']}: outstanding {r['outstanding']} {r['currency']}; overdue {r['overdue']}; {r['bills']} bills"
			)
		for r in report["received_unbilled"][:10]:
			out.append(
				f"Received/unbilled estimate: {r['supplier']}: {r['estimated_unbilled_net']} (receipt net basis)"
			)
		out.append(report["cash_reconciliation"])
	return out


def queue_digests(branches, channel, business_key):
	config = settings()
	if not config.enabled or not config.discord_enabled:
		return []
	destinations = defaultdict(list)
	for branch in branches:
		doc = frappe.get_doc("Branch", branch)
		if not doc.get("custom_operations_enabled"):
			continue
		address = route(branch, channel)
		if address:
			destinations[fingerprint(address)].append(branch)
	created = []
	for dest, names in destinations.items():
		# Initial daily packs are immutable; explicit revision keys can be queued later.
		text = []
		snapshots = []
		for branch in sorted(names):
			snap = latest(branch)
			if (
				not snap
				or (now_datetime() - frappe.utils.get_datetime(snap["cutoff"])).total_seconds() > 7200
			):
				text.append(f"**{branch} — {channel}: stale or missing evidence; report unavailable**")
			elif channel == "Purchase":
				snapshots.append(snap)
			else:
				text += lines(snap, channel)
		if channel == "Purchase":
			vendors = defaultdict(list)
			for snap in snapshots:
				for vendor in snap["reports"].get("Purchase", {}).get("vendors", []):
					vendors[vendor["vendor"]].append((snap, vendor))
			text += [
				"**Vendor order proposals — branch destinations retained**",
				"Stock-unit quantities; historical price estimates. Record orders already placed before ordering again.",
			]
			for name, packs in sorted(vendors.items()):
				text.append(f"**{name}** — estimated total {sum(v['estimated_value'] for _, v in packs):.2f}")
				for snap, vendor in packs:
					text.append(
						f"**{snap['branch']}** — ready {len(vendor['ready'])}; review {len(vendor['review'])}"
					)
					for row in vendor["ready"][:15]:
						text.append(
							f"{row['item_name']} [{row['item_code']}]: {row['suggested_qty']} {row['stock_uom']}; {row['daily_demand']}/day; cover {row['days_cover']}d; estimate {row['estimated_value']}"
						)
					for row in vendor["review"][:5]:
						text.append(f"REVIEW {row['item_code']}: {'; '.join(row['flags'])}")
					text.append(operations_url() + "?branch=" + requests.utils.quote(snap["branch"]))
			if not vendors:
				text.append(
					"No supported new vendor order lines. Check any unavailable branch reports and existing commitments."
				)
		pages = chunks(text)
		max_pages = int(config.get("max_discord_pages") or 8)
		if len(pages) > max_pages:
			pages = pages[: max_pages - 1] + [
				f"Additional {channel} detail is available in the authenticated Operations Console. Full vendor orders and CSV downloads are retained there. This Discord digest is limited to {max_pages} messages.\n"
				+ operations_url()
			]
		for page, content in enumerate(pages):
			key = fingerprint([channel, str(business_key), dest, page])
			existing = frappe.db.get_value("Retail Discord Delivery", {"delivery_key": key}, "name")
			if existing:
				previous = frappe.get_doc("Retail Discord Delivery", existing)

				def normalized(text):
					return "\n".join(line for line in text.splitlines() if not line.startswith("Evidence:"))

				if previous.status in ("Sent", "Pending", "Retry") and normalized(
					json.loads(previous.payload)["content"]
				) != normalized(content):
					previous.payload = frappe.as_json({"content": content, "allowed_mentions": {"parse": []}})
					previous.status = "Pending"
					write_internal(previous)
				continue
			doc = frappe.get_doc(
				dict(
					doctype="Retail Discord Delivery",
					delivery_key=key,
					channel=channel,
					route_branch=names[0],
					branches=frappe.as_json(sorted(names)),
					destination_hash=dest,
					payload=frappe.as_json({"content": content, "allowed_mentions": {"parse": []}}),
					status="Pending",
				)
			)
			write_internal(doc)
			created.append(doc.name)
	return created


def deliver(name, client=None):
	client = client or requests
	config = settings()
	if not config.enabled or not config.discord_enabled:
		return "Disabled"
	lock = "retail-discord:" + fingerprint([frappe.local.site, name])[:40]
	if not frappe.db.sql("SELECT GET_LOCK(%s,0)", lock)[0][0]:
		return "Busy"
	try:
		doc = frappe.get_doc("Retail Discord Delivery", name)
		if doc.status not in ("Pending", "Retry"):
			return doc.status
		if doc.next_attempt and doc.next_attempt > now_datetime():
			return "Waiting"
		address = route(doc.route_branch, doc.channel)
		branches = json.loads(doc.branches)
		if (
			not address
			or fingerprint(address) != doc.destination_hash
			or any(
				not frappe.db.get_value("Branch", b, "custom_operations_enabled")
				or fingerprint(route(b, doc.channel) or "") != doc.destination_hash
				for b in branches
			)
		):
			doc.status = "Skipped"
			doc.error_code = "Destination changed or branch disabled"
			write_internal(doc)
			frappe.db.commit()
			return doc.status
		doc.status = "Sending"
		doc.attempts = (doc.attempts or 0) + 1
		write_internal(doc)
		frappe.db.commit()  # Durable ambiguity marker before the external side effect.
		try:
			endpoint = address + (
				"/messages/" + doc.discord_message_id if doc.discord_message_id else "?wait=true"
			)
			method = client.patch if doc.discord_message_id else client.post
			response = method(endpoint, json=json.loads(doc.payload), timeout=(5, 20), allow_redirects=False)
		except requests.RequestException:
			doc.status = "Uncertain"
			doc.error_code = "Network outcome unknown; inspect destination before retry"
		else:
			if response.status_code == 200:
				try:
					doc.discord_message_id = str(response.json()["id"])
					doc.status = "Sent"
					doc.sent_at = now_datetime()
					doc.error_code = ""
				except (ValueError, KeyError, TypeError):
					doc.status = "Uncertain"
					doc.error_code = "Accepted without usable message identifier"
			elif response.status_code == 429:
				try:
					delay = max(1, min(float(response.json().get("retry_after", 60)), 3600))
				except (ValueError, TypeError):
					delay = 60
				doc.status = "Retry"
				doc.next_attempt = now_datetime() + timedelta(seconds=delay)
				doc.error_code = "Rate limited"
			elif 500 <= response.status_code < 600:
				doc.status = "Uncertain"
				doc.error_code = "Server outcome unknown; inspect destination before retry"
			else:
				doc.status = "Failed"
				doc.error_code = "Discord HTTP " + str(response.status_code)
		write_internal(doc)
		frappe.db.commit()
		return doc.status
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock)


def queue_urgent(snapshot, baseline=False):
	"""Changed urgent findings only; initial historical backlog stays in the digest."""
	if baseline or not settings().discord_enabled:
		# Seed the historical baseline even while sending is off. Otherwise the
		# next evaluation would mistake the whole old backlog for new alerts.
		frappe.db.sql(
			"UPDATE `tabRetail Operations Issue` SET notified_hash=signal_hash,last_notified=%s WHERE branch=%s",
			(now_datetime(), snapshot["branch"]),
		)
		return []
	rows = frappe.get_all(
		"Retail Operations Issue",
		filters={
			"branch": snapshot["branch"],
			"status": ["not in", ["Resolved", "Muted"]],
			"severity": "Urgent",
		},
		fields=["name", "channel", "subject", "signal_hash", "evidence", "notified_hash", "last_notified"],
	)
	queued = []
	for issue in rows:
		if issue.signal_hash == issue.notified_hash:
			continue
		if issue.last_notified and (now_datetime() - issue.last_notified).total_seconds() < 21600:
			continue
		address = route(snapshot["branch"], issue.channel)
		if not address:
			continue
		evidence = json.loads(issue.evidence)
		text = [
			f"**{snapshot['branch']} — {issue.subject}**",
			str(evidence.get("item_name") or evidence.get("name") or ""),
			f"Recorded stock: {evidence.get('actual_qty', '—')}; cover: {evidence.get('days_cover', '—')} days; refund amount: {evidence.get('amount', '—')}.",
			"; ".join(evidence.get("flags") or []),
			operations_url() + "?branch=" + requests.utils.quote(snapshot["branch"]),
		]
		key = fingerprint(["urgent", issue.name, issue.signal_hash])
		if not frappe.db.exists("Retail Discord Delivery", {"delivery_key": key}):
			doc = write_internal(
				frappe.get_doc(
					dict(
						doctype="Retail Discord Delivery",
						delivery_key=key,
						channel=issue.channel,
						route_branch=snapshot["branch"],
						branches=frappe.as_json([snapshot["branch"]]),
						destination_hash=fingerprint(address),
						payload=frappe.as_json(
							{"content": "\n".join(text)[:1900], "allowed_mentions": {"parse": []}}
						),
						status="Pending",
					)
				)
			)
			queued.append(doc.name)
		# This marks durable queueing; delivery status remains visible separately.
		frappe.db.set_value(
			"Retail Operations Issue",
			issue.name,
			{"notified_hash": issue.signal_hash, "last_notified": now_datetime()},
			update_modified=False,
		)
	return queued
