"""Scheduled maintenance for the ai/ module. Wired into hooks.py's scheduler_events."""

from __future__ import annotations

import frappe
from frappe.utils import add_days, add_months, getdate, now_datetime, today

RETENTION_DAYS = 30


def purge_old_ai_messages():
	"""Daily job: deletes AI Assistant Message rows older than RETENTION_DAYS so the
	audit log doesn't grow unbounded. This is the only place these records are ever
	removed - nothing in api.py deletes them (Clear Conversation in the UI only
	clears that tab's own view, not the server-side record)."""
	cutoff = add_days(now_datetime(), -RETENTION_DAYS)
	frappe.db.delete("AI Assistant Message", {"creation": ["<", cutoff]})


def run_scheduled_questions():
	"""Daily scheduler job: finds enabled Scheduled Question rows that are due,
	runs each one as its owning user, emails the answer, updates last_run/next_run."""
	from aimatic.ai.api import ask

	sq_list = frappe.get_all(
		"Scheduled Question",
		filters={"enabled": 1},
		fields=["name", "question", "frequency", "recipients", "user", "last_run"],
	)

	today_date = getdate(today())

	for sq in sq_list:
		due = False
		last_run = sq.last_run
		freq = sq.frequency

		if not last_run:
			due = True
		elif freq == "Daily":
			due = add_days(getdate(last_run), 1) <= today_date
		elif freq == "Weekly":
			due = add_days(getdate(last_run), 7) <= today_date
		elif freq == "Monthly":
			due = add_months(getdate(last_run), 1) <= today_date

		if not due:
			continue

		# Everything for this one question - the ask() call, the email, and
		# the last_run/next_run update - lives in a single try/except so a
		# failure anywhere (including frappe.sendmail, e.g. no outgoing Email
		# Account configured on this site) logs and moves on to the next
		# question rather than aborting the whole daily job for every other
		# user's scheduled questions too. Confirmed live: an unconfigured
		# Email Account raised straight out of the old, narrower try/except
		# that only wrapped the ask() call, killing the rest of the run.
		original_user = frappe.session.user
		try:
			frappe.set_user(sq.user)
			answer = ask(message=sq.question)

			summary = answer.get("summary", "")
			kpis = answer.get("kpis", [])
			kpi_lines = "\n".join([f"- {k.get('label', '')}: {k.get('value', '')}" for k in kpis])
			question_preview = (sq.question[:80] + "...") if len(sq.question) > 80 else sq.question

			recipients = [r.strip() for r in sq.recipients.split(",") if r.strip()]
			if recipients:
				frappe.sendmail(
					recipients=recipients,
					subject=f"Scheduled Report: {question_preview}",
					message=f"<pre>{summary}</pre>\n\n<h4>KPIs:</h4>\n<pre>{kpi_lines}</pre>",
				)

			now = now_datetime()
			if freq == "Daily":
				next_run = add_days(now, 1)
			elif freq == "Weekly":
				next_run = add_days(now, 7)
			elif freq == "Monthly":
				next_run = add_months(now, 1)
			else:
				next_run = add_days(now, 1)

			frappe.db.set_value("Scheduled Question", sq.name, {"last_run": now, "next_run": next_run})
			frappe.db.commit()
		except Exception:
			frappe.log_error(
				title=f"Scheduled Question failed: {sq.name}",
				message=frappe.get_traceback(),
			)
			frappe.db.rollback()
		finally:
			if frappe.session.user != original_user:
				frappe.set_user(original_user)
		frappe.db.commit()


def check_alert_rules():
	"""Daily scheduler job: evaluates each enabled AI Alert Rule as its owning user,
	emails insights if any detector fires, updates last_triggered."""
	from aimatic.ai.insight_generator import (
		_detect_branch_underperformance,
		_detect_dead_stock,
		_detect_high_return_rate,
		_detect_low_gross_margin,
		_detect_negative_vendor_margin,
		_detect_payables_concentration,
		_detect_price_increases,
		_detect_stockout_risk,
	)
	from aimatic.ai.tools import (
		get_branch_comparison,
		get_gross_margin_overview,
		get_inventory_vs_sales,
		get_outstanding_payables_overview,
		get_returns_overview,
		get_sales_overview,
		rank_vendors,
	)
	from aimatic.ai.tools_extended import (
		get_dead_stock_detail,
		get_price_increases,
	)

	# Map rule_name -> (tool_function, detector_function, tool_result_key)
	# threshold_override is stored on the doctype but NOT applied yet (detectors don't accept it)
	RULE_MAP = {
		"dead_stock": (get_dead_stock_detail, _detect_dead_stock, "get_dead_stock_detail"),
		"price_increases": (get_price_increases, _detect_price_increases, "get_price_increases"),
		"stockout_risk": (get_inventory_vs_sales, _detect_stockout_risk, "get_inventory_vs_sales"),
		"negative_vendor_margin": (rank_vendors, _detect_negative_vendor_margin, "rank_vendors"),
		"branch_underperformance": (
			get_branch_comparison,
			_detect_branch_underperformance,
			"get_branch_comparison",
		),
		"low_gross_margin": (
			get_gross_margin_overview,
			_detect_low_gross_margin,
			"get_gross_margin_overview",
		),
		"payables_concentration": (
			get_outstanding_payables_overview,
			_detect_payables_concentration,
			"get_outstanding_payables_overview",
		),
		"high_return_rate": (None, _detect_high_return_rate, None),  # needs two tools
	}

	rules = frappe.get_all(
		"AI Alert Rule",
		filters={"enabled": 1},
		fields=["name", "rule_name", "recipients", "user", "threshold_override"],
	)

	original_user = frappe.session.user

	for rule in rules:
		rule_name = rule.rule_name
		if rule_name not in RULE_MAP:
			frappe.log_error(
				title=f"AI Alert Rule unknown rule_name: {rule.name}",
				message=f"rule_name '{rule_name}' not in RULE_MAP",
			)
			continue

		tool_fn, detector_fn, tool_key = RULE_MAP[rule_name]

		# Same reasoning as run_scheduled_questions above: everything for this
		# one rule - the tool call, the detector, the email, and the
		# last_triggered update - lives in a single try/except so a failure
		# anywhere (including frappe.sendmail) logs and moves on to the next
		# rule rather than aborting the check for every other user's rules.
		try:
			frappe.set_user(rule.user)

			tool_results = {}
			if rule_name == "high_return_rate":
				# Special case: needs two tool calls
				tool_results["get_returns_overview"] = get_returns_overview()
				tool_results["get_sales_overview"] = get_sales_overview()
			elif rule_name == "branch_underperformance":
				# _detect_branch_underperformance itself prefers
				# get_branch_comparison over get_sales_overview when both are
				# present - calling the more direct/reliable one is enough,
				# no try/except needed (this tool doesn't meaningfully "fail").
				tool_results["get_branch_comparison"] = get_branch_comparison()
			else:
				tool_results[tool_key] = tool_fn()

			insights = detector_fn(tool_results)

			if insights:
				recipients = [r.strip() for r in rule.recipients.split(",") if r.strip()]
				if recipients:
					lines = [f"<b>{ins.title}</b>: {ins.description}" for ins in insights]
					frappe.sendmail(
						recipients=recipients,
						subject=f"AI Alert: {rule_name.replace('_', ' ').title()}",
						message="<br><br>".join(lines),
					)
				frappe.db.set_value("AI Alert Rule", rule.name, "last_triggered", now_datetime())
				frappe.db.commit()
		except Exception:
			frappe.log_error(
				title=f"AI Alert Rule check failed: {rule.name}",
				message=frappe.get_traceback(),
			)
			frappe.db.rollback()
		finally:
			if frappe.session.user != original_user:
				frappe.set_user(original_user)
