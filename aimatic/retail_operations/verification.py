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
