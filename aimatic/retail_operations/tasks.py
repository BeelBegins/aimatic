from __future__ import annotations

import frappe
from frappe.utils import get_datetime, now_datetime, today

from aimatic.retail_operations.calculations import CHANNELS


def evaluate_branch(branch):
	from aimatic.retail_operations.service import build, generate_requests, settings

	user = frappe.session.user
	frappe.set_user("Administrator")
	lock = "retail-eval:" + frappe.generate_hash(branch, 12)
	try:
		if not frappe.db.sql("SELECT GET_LOCK(%s,0)", lock)[0][0]:
			return
		refresh_commitments(branch)
		build(branch)
		frappe.db.commit()
		config = settings()
		if config.enabled and config.automatic_requests:
			generate_requests(branch)
	except Exception as exc:
		frappe.db.rollback()
		frappe.db.set_single_value(
			"Retail Operations Settings", "last_error", "Branch " + branch + ": " + type(exc).__name__
		)
		frappe.db.commit()
		raise
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock)
		frappe.set_user(user)


def tick():
	from aimatic.retail_operations.discord import deliver, queue_digests
	from aimatic.retail_operations.service import latest, settings

	config = settings()
	if not config.enabled:
		return
	branches = frappe.get_all("Branch", filters={"custom_operations_enabled": 1}, pluck="name")
	now = now_datetime()
	for branch in branches:
		snap = latest(branch)
		if not snap or (now - get_datetime(snap["cutoff"])).total_seconds() > 3500:
			frappe.enqueue(
				"aimatic.retail_operations.tasks.evaluate_branch",
				branch=branch,
				queue="long",
				job_id="retail-operations-" + branch,
				deduplicate=True,
				enqueue_after_commit=True,
			)
	if config.discord_enabled:
		for channel in CHANNELS:
			target = str(config.get(channel.lower() + "_time") or "09:00:00")
			if str(now.time())[:8] >= target.zfill(8):
				queue_digests(branches, channel, today())
		# Sending records left by a dead worker are not automatically retried.
		frappe.db.sql("""UPDATE `tabRetail Discord Delivery` SET status='Uncertain',
            error_code='Worker interrupted after send started; inspect Discord before retry'
            WHERE status='Sending' AND modified<DATE_SUB(NOW(),INTERVAL 10 MINUTE)""")
		pending = frappe.get_all(
			"Retail Discord Delivery",
			filters={"status": ["in", ["Pending", "Retry"]]},
			pluck="name",
			limit_page_length=50,
		)
		frappe.db.commit()
		for name in pending:
			deliver(name)
	frappe.db.set_single_value("Retail Operations Settings", "last_evaluation", now)


def refresh_commitments(branch):
	from aimatic.retail_operations.commitments import received

	for name in frappe.get_all(
		"Retail Purchase Commitment",
		filters={"branch": branch, "status": ["in", ["Placed", "Delayed"]]},
		pluck="name",
	):
		doc = frappe.get_doc("Retail Purchase Commitment", name)
		total = 0
		done = 0
		for line in doc.items:
			qty = min(received(doc, line), float(line.qty))
			total += float(line.qty)
			done += qty
			if abs(float(line.received_qty or 0) - qty) > 0.00001:
				frappe.db.set_value(
					"Retail Commitment Item", line.name, "received_qty", qty, update_modified=False
				)
		state = (
			"Received" if total and done >= total else ("Partially received" if done else "Awaiting receipt")
		)
		if doc.fulfilment_status != state:
			frappe.db.set_value("Retail Purchase Commitment", doc.name, "fulfilment_status", state)


def drain_deliveries():
	from aimatic.retail_operations.discord import deliver
	from aimatic.retail_operations.service import settings

	config = settings()
	if not config.enabled or not config.discord_enabled:
		return
	names = frappe.get_all(
		"Retail Discord Delivery",
		filters={"status": ["in", ["Pending", "Retry"]]},
		pluck="name",
		order_by="creation asc",
		limit_page_length=50,
	)
	for name in names:
		deliver(name)
