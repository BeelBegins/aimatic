"""Durable webhook delivery. Secrets never enter payloads, errors or AI context."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

import frappe
import requests
from frappe.utils import get_url, now_datetime

from aimatic.retail_operations.calculations import fingerprint, webhook_valid
from aimatic.retail_operations.service import latest, settings, write_internal


def operations_url():
	return (settings().get("public_base_url") or get_url()).rstrip("/") + "/app/retail-operations-console"


def route(branch, channel):
	doc = frappe.get_doc("Branch", branch)
	value = doc.get_password("custom_discord_" + channel.lower(), raise_exception=False)
	return value if value and webhook_valid(value) else None


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
		from aimatic.retail_operations.presentation import digest

		snapshots = []
		for branch in sorted(names):
			snap = latest(branch)
			if (
				not snap
				or (now_datetime() - frappe.utils.get_datetime(snap["cutoff"])).total_seconds() > 7200
			):
				snap = dict(branch=branch, reports={}, cutoff="")
			snapshots.append(snap)
		pages = digest(snapshots, channel, operations_url(), int(config.get("max_discord_pages") or 8))
		for page, body in enumerate(pages):
			key = fingerprint([channel, str(business_key), dest, page])
			existing = frappe.db.get_value("Retail Discord Delivery", {"delivery_key": key}, "name")
			if existing:
				previous = frappe.get_doc("Retail Discord Delivery", existing)

				# Evidence timestamps may advance, but a layout/data change edits the same message.
				def normalized(value):
					value = json.loads(json.dumps(value))
					for embed in value.get("embeds", []):
						embed["description"] = "\n".join(
							line
							for line in embed.get("description", "").splitlines()
							if not line.startswith(("Evidence:", "Oldest evidence:"))
						)
					return json.dumps(value, sort_keys=True)

				if previous.status in ("Sent", "Pending", "Retry") and normalized(
					json.loads(previous.payload)
				) != normalized(body):
					previous.payload = frappe.as_json(body)
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
					payload=frappe.as_json(body),
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
		from aimatic.retail_operations.presentation import urgent

		body = urgent(snapshot, issue, evidence, operations_url())
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
						payload=frappe.as_json(body),
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
