"""Small administrative deployment checks; no credentials or raw reports in output."""

from __future__ import annotations

import time

import frappe


def preview_all(persist=True):
	frappe.only_for("System Manager")
	from aimatic.retail_operations.service import build, settings

	results = []
	for branch in frappe.get_all("Branch", pluck="name"):
		started = time.monotonic()
		snapshot = build(branch, persist=persist)
		if persist:
			frappe.db.commit()
		inv = snapshot["reports"].get("Inventory", {})
		results.append(
			dict(
				branch=branch,
				run=snapshot.get("run"),
				errors=snapshot["errors"],
				seconds=round(time.monotonic() - started, 2),
				items=len(inv.get("rows", [])),
				vendors=len(inv.get("vendors", [])),
				summary=inv.get("summary"),
				sales_counters=len(snapshot["reports"].get("Sales", {}).get("counters", [])),
			)
		)
	return dict(
		branches=results,
		discord_enabled=bool(settings().discord_enabled),
		automatic_requests=bool(settings().automatic_requests),
	)


def enable_evaluation(public_base_url):
	frappe.only_for("System Manager")
	doc = frappe.get_single("Retail Operations Settings")
	if doc.discord_enabled or doc.automatic_requests:
		frappe.throw("This activation helper is for evaluation-only startup")
	doc.public_base_url = public_base_url
	doc.enabled = 1
	doc.save()
	for branch in frappe.get_all("Branch", pluck="name"):
		warehouse = frappe.db.get_value("Branch", branch, "finished_goods_warehouse")
		if warehouse and not frappe.db.get_value("Warehouse", warehouse, "disabled"):
			frappe.db.set_value("Branch", branch, "custom_operations_enabled", 1)
	return {"evaluation_enabled": True, "discord_enabled": False, "automatic_requests": False}


def runtime_summary(enqueue=False):
	frappe.only_for("System Manager")
	from frappe.utils.background_jobs import get_job_status, get_queue, get_workers
	from frappe.utils.scheduler import is_scheduler_disabled

	from aimatic.retail_operations.service import settings

	config = settings()
	value = dict(
		evaluation_enabled=bool(config.enabled),
		discord_enabled=bool(config.discord_enabled),
		automatic_requests=bool(config.automatic_requests),
		long_workers=len(get_workers(queue=get_queue("long"))),
		scheduler_disabled=is_scheduler_disabled(verbose=False),
		jobs=frappe.get_all(
			"Scheduled Job Type",
			filters={"method": ["like", "aimatic.retail_operations.%"]},
			fields=["method", "stopped", "last_execution", "frequency"],
		),
		branches=frappe.get_all(
			"Branch", fields=["name", "custom_operations_enabled", "custom_operations_auto_requests"]
		),
		generated_material_requests=frappe.db.count(
			"Material Request", {"custom_operations_run": ["is", "set"]}
		),
		outbox_records=frappe.db.count("Retail Discord Delivery"),
	)
	if enqueue:
		frappe.enqueue(
			"aimatic.retail_operations.tasks.evaluate_branch",
			branch="S1 - Ghouri Town VIP",
			queue="long",
			job_id="retail-operations-verification",
			deduplicate=True,
			enqueue_after_commit=False,
		)
	value["verification_job_status"] = str(get_job_status("retail-operations-verification"))
	return value


def destination_summary(source_branch="S1 - Ghouri Town VIP"):
	frappe.only_for("System Manager")
	from aimatic.retail_operations.calculations import CHANNELS, fingerprint, webhook_valid

	source = frappe.get_doc("Branch", source_branch)
	result = {}
	for channel in CHANNELS:
		field = "custom_discord_" + channel.lower()
		address = source.get_password(field, raise_exception=False)
		matches = 0
		for name in frappe.get_all("Branch", filters={"company": source.company}, pluck="name"):
			other = frappe.get_doc("Branch", name).get_password(field, raise_exception=False)
			matches += bool(address and other and fingerprint(address) == fingerprint(other))
		result[channel] = dict(
			configured=bool(address),
			valid=bool(address and webhook_valid(address)),
			matching_branches=matches,
		)
	return result


def activate_discord():
	"""Publish the initial, current reports after an administrator configures routes."""
	frappe.only_for("System Manager")
	from frappe.utils import today

	from aimatic.retail_operations.calculations import CHANNELS
	from aimatic.retail_operations.discord import queue_digests, route
	from aimatic.retail_operations.service import latest
	from aimatic.retail_operations.tasks import drain_deliveries

	branches = frappe.get_all("Branch", filters={"custom_operations_enabled": 1}, pluck="name")
	if not branches:
		frappe.throw("No enabled branches")
	for branch in branches:
		snapshot = latest(branch)
		if not snapshot or snapshot["errors"] or any(not route(branch, c) for c in CHANNELS):
			frappe.throw("Every enabled branch needs current reports and four valid destinations")
	config = frappe.get_single("Retail Operations Settings")
	config.discord_enabled = 1
	config.save()
	for channel in CHANNELS:
		queue_digests(branches, channel, "initial-" + today())
	frappe.db.commit()
	drain_deliveries()
	return delivery_summary()


def delivery_summary():
	frappe.only_for("System Manager")
	return frappe.db.sql(
		"""SELECT channel, status, COUNT(*) AS messages,
		SUM(CASE WHEN discord_message_id IS NOT NULL AND discord_message_id != '' THEN 1 ELSE 0 END) AS confirmed_ids
		FROM `tabRetail Discord Delivery` GROUP BY channel, status ORDER BY channel, status""",
		as_dict=True,
	)
