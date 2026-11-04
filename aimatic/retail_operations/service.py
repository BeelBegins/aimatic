from __future__ import annotations

import base64
import gzip
import json
from collections import defaultdict

import frappe
from frappe.utils import add_days, flt, getdate, now_datetime, today

from aimatic.retail_operations import data
from aimatic.retail_operations.calculations import CHANNELS, VERSION, fingerprint, number, vendor_packs
from aimatic.retail_operations.permissions import check


def pack_snapshot(value):
	raw = frappe.as_json(value).encode()
	return json.dumps(
		{"encoding": "gzip-base64-v1", "data": base64.b64encode(gzip.compress(raw, mtime=0)).decode()}
	)


def unpack_snapshot(value):
	doc = json.loads(value)
	if doc.get("encoding") == "gzip-base64-v1":
		return json.loads(gzip.decompress(base64.b64decode(doc["data"])))
	return doc


def write_internal(doc):
	doc.flags.operations_internal = True
	return doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)


def settings():
	return frappe.get_single("Retail Operations Settings")


def build(branch_name, persist=True):
	branch = check(branch_name)
	config = settings()
	business_date = add_days(getdate(today()), -1)
	snapshot = dict(
		branch=branch.name,
		company=branch.company,
		warehouse=branch.finished_goods_warehouse,
		cutoff=str(now_datetime()),
		business_date=str(business_date),
		version=VERSION,
		reports={},
		errors={},
	)
	# Each channel has a separate failure state; accounts failure never becomes zero dues.
	for channel, func in (
		("Inventory", lambda: data.inventory(branch, config)),
		("Sales", lambda: data.sales(branch, config, business_date)),
		("Accounts", lambda: data.accounts(branch, business_date)),
	):
		try:
			snapshot["reports"][channel] = func()
		except Exception as exc:
			snapshot["errors"][channel] = type(exc).__name__
	if "Inventory" in snapshot["reports"]:
		inv = snapshot["reports"]["Inventory"]
		snapshot["reports"]["Purchase"] = dict(
			vendors=inv["vendors"],
			warnings=inv["warnings"],
			history_to=inv["history_to"],
			commitments=frappe.get_all(
				"Retail Purchase Commitment",
				filters={"branch": branch.name, "status": ["in", ["Placed", "Delayed"]]},
				fields=[
					"name",
					"supplier",
					"status",
					"fulfilment_status",
					"placed_on",
					"expected_on",
					"external_reference",
				],
			),
			open_orders=[
				dict(order, item_code=row["item_code"])
				for row in inv["rows"]
				for order in row.get("open_orders", [])
			],
			estimated_currency=frappe.db.get_value("Company", branch.company, "default_currency"),
		)
	else:
		snapshot["errors"]["Purchase"] = "Inventory evidence unavailable"
	if not persist:
		return snapshot
	run = write_internal(
		frappe.get_doc(
			dict(
				doctype="Retail Operations Run",
				branch=branch.name,
				company=branch.company,
				cutoff=snapshot["cutoff"],
				business_date=business_date,
				calculation_version=VERSION,
				snapshot=pack_snapshot(snapshot),
				status="Partial" if snapshot["errors"] else "Complete",
				error=frappe.as_json(snapshot["errors"]) if snapshot["errors"] else "",
			)
		)
	)
	snapshot["run"] = run.name
	baseline = not frappe.db.count("Retail Operations Issue", {"branch": branch.name})
	sync_issues(snapshot, run.name)
	if settings().enabled and branch.get("custom_operations_enabled"):
		from aimatic.retail_operations.discord import queue_urgent

		queue_urgent(snapshot, baseline=baseline)
	return snapshot


def sync_issues(snapshot, run_name):
	branch = snapshot["branch"]
	seen = []
	inv = snapshot["reports"].get("Inventory")
	signals = []
	if inv is not None:
		for r in inv["rows"]:
			if r["status"] in (
				"Negative stock",
				"Stockout risk",
				"Low cover",
				"Overstock",
			) or r.get("reference_group"):
				# All static anomalies remain one issue per product and branch.
				signals.append(
					(
						"Inventory",
						r["item_code"],
						r["status"],
						r,
						"Urgent" if r["status"] == "Stockout risk" else "Warning",
					)
				)
	sales = snapshot["reports"].get("Sales")
	if sales:
		for refund in sales["large_refunds"]:
			signals.append(("Sales", "refund:" + refund["name"], "Large refund", dict(refund), "Urgent"))
		for item in sales["margins"].get("negative", []):
			signals.append(
				(
					"Sales",
					"margin:" + sales["date"] + ":" + item["item_code"],
					"Negative recorded margin",
					item,
					"Warning",
				)
			)
	existing = {
		r.issue_key: r
		for r in frappe.get_all(
			"Retail Operations Issue",
			filters={"branch": branch},
			fields=["name", "issue_key", "signal_hash", "status", "mute_until"],
		)
	}
	unchanged = []
	inserts = []
	now = now_datetime()
	columns = [
		"name",
		"creation",
		"modified",
		"owner",
		"modified_by",
		"docstatus",
		"branch",
		"company",
		"issue_key",
		"channel",
		"subject",
		"evidence",
		"severity",
		"signal_hash",
		"last_seen",
		"last_changed",
		"run",
		"status",
	]
	for channel, ref, subject, evidence, severity in signals:
		key = fingerprint([branch, channel, ref])
		seen.append(key)
		signal = fingerprint(
			[
				subject,
				round(number(evidence.get("available_qty")), 0),
				round(number(evidence.get("daily_demand")), 2),
				evidence.get("flags"),
				evidence.get("amount"),
				evidence.get("profit"),
			]
		)
		old = existing.get(key)
		if not old:
			values = [
				"OPS-" + key[:24],
				now,
				now,
				"Administrator",
				"Administrator",
				0,
				branch,
				snapshot["company"],
				key,
				channel,
				subject,
				frappe.as_json(evidence),
				severity,
				signal,
				now,
				now,
				run_name,
				"New",
			]
			inserts.append(values)
			continue
		expired = old.status == "Muted" and old.mute_until and old.mute_until < now
		if old.signal_hash == signal and old.status != "Resolved" and not expired:
			unchanged.append(old.name)
			continue
		issue = frappe.get_doc("Retail Operations Issue", old.name)
		issue.update(
			dict(
				branch=branch,
				company=snapshot["company"],
				issue_key=key,
				channel=channel,
				subject=subject,
				evidence=frappe.as_json(evidence),
				severity=severity,
				signal_hash=signal,
				last_seen=now,
				last_changed=now,
				run=run_name,
			)
		)
		if old.status == "Resolved" or expired:
			issue.status = "New"
			issue.mute_until = None
		write_internal(issue)
	# Generated findings have no transaction lifecycle; batch their initial audit
	# metadata and unchanged observations rather than producing thousands of Versions.
	if inserts:
		frappe.db.bulk_insert("Retail Operations Issue", columns, inserts, chunk_size=500)
	for start in range(0, len(unchanged), 500):
		frappe.db.sql(
			"UPDATE `tabRetail Operations Issue` SET last_seen=%s WHERE name IN %s",
			(now, tuple(unchanged[start : start + 500])),
		)
	# Resolve inventory findings only if a complete inventory calculation succeeded.
	if inv is not None:
		active = frappe.get_all(
			"Retail Operations Issue",
			filters={"branch": branch, "channel": "Inventory", "status": ["not in", ["Resolved", "Muted"]]},
			fields=["name", "issue_key"],
		)
		seen = set(seen)
		for row in active:
			if row.issue_key not in seen:
				doc = frappe.get_doc("Retail Operations Issue", row.name)
				doc.status = "Resolved"
				doc.reason = "No longer present in complete inventory evaluation"
				doc.last_changed = now_datetime()
				write_internal(doc)


def latest(branch):
	name = frappe.db.get_value("Retail Operations Run", {"branch": branch}, "name", order_by="creation desc")
	if not name:
		return None
	doc = frappe.get_doc("Retail Operations Run", name)
	value = unpack_snapshot(doc.snapshot)
	value["run"] = name
	return value


def generate_requests(branch_name):
	branch = check(branch_name, "Purchase", write=True)
	config = settings()
	if (
		not config.enabled
		or not config.automatic_requests
		or not branch.get("custom_operations_auto_requests")
	):
		return []
	# A database advisory lock covers all donors/branches on this site. It survives
	# neither process death nor connection loss, and is explicitly released.
	lock = "retail-mr:" + frappe.local.site
	if not frappe.db.sql("SELECT GET_LOCK(%s,0)", lock)[0][0]:
		return []
	try:
		inv = data.inventory(branch, config)
		groups = defaultdict(list)
		rules = {
			r.item_code: r
			for r in frappe.get_all(
				"Retail Replenishment Rule",
				filters={"branch": branch.name, "enabled": 1, "reviewed": 1, "automatic_request": 1},
				fields=["name", "item_code", "supplier", "source_warehouse"],
			)
		}
		for row in sorted(inv["rows"], key=lambda r: r["item_code"]):
			rule = rules.get(row["item_code"])
			if not rule or not row["auto_eligible"]:
				continue
			if frappe.db.exists(
				"Item Reorder", {"parent": row["item_code"], "warehouse": branch.finished_goods_warehouse}
			):
				continue
			# Current locking reads protect against stock changes after calculation.
			current = frappe.db.sql(
				"SELECT actual_qty,reserved_qty FROM tabBin WHERE item_code=%s AND warehouse=%s FOR UPDATE",
				(row["item_code"], branch.finished_goods_warehouse),
				as_dict=True,
			)
			if "actual_qty" in row and (
				not current
				or abs(float(current[0].actual_qty) - float(row["actual_qty"])) > 0.00001
				or abs(float(current[0].reserved_qty) - float(row.get("reserved_qty") or 0)) > 0.00001
			):
				continue
			existing = frappe.db.sql(
				"""SELECT mri.name FROM `tabMaterial Request Item` mri
                JOIN `tabMaterial Request` mr ON mr.name=mri.parent
                WHERE mr.docstatus=1 AND mr.company=%s AND mri.warehouse=%s AND mri.item_code=%s
                AND mr.status NOT IN ('Stopped','Cancelled','Ordered','Received','Issued','Transferred')
                AND mri.stock_qty>mri.ordered_qty LIMIT 1 FOR UPDATE""",
				(branch.company, branch.finished_goods_warehouse, row["item_code"]),
			)
			if existing:
				continue
			# Provisional fallback policies need review in a rule, but stock evidence
			# must still be sound. No group/reference netting or blind negatives.
			groups[(rule.supplier, rule.source_warehouse)].append((rule, row))
		created = []
		for (supplier, source), items in groups.items():
			mr = frappe.new_doc("Material Request")
			mr.update(
				dict(
					company=branch.company,
					branch=branch.name,
					material_request_type="Material Transfer" if source else "Purchase",
					transaction_date=today(),
					set_warehouse=branch.finished_goods_warehouse,
					custom_operations_vendor=supplier or None,
					set_from_warehouse=source or None,
					custom_operations_run=fingerprint([branch.name, str(now_datetime()), supplier]),
					schedule_date=add_days(today(), 1),
				)
			)
			for rule, row in items:
				mr.append(
					"items",
					dict(
						item_code=row["item_code"],
						qty=row["suggested_qty"],
						uom=row["stock_uom"],
						stock_uom=row["stock_uom"],
						conversion_factor=1,
						warehouse=branch.finished_goods_warehouse,
						from_warehouse=source or None,
						branch=branch.name,
						cost_center=branch.cost_center,
						schedule_date=add_days(today(), 1),
					),
				)
			mr.remarks = (
				"Automatic replenishment requirement. Schedule is a requested date, not a vendor delivery promise. Vendor: "
				+ (supplier or source)
			)
			mr.insert(ignore_permissions=True)
			mr.submit()
			for rule, row in items:
				frappe.db.set_value("Retail Replenishment Rule", rule.name, "last_material_request", mr.name)
			created.append(mr.name)
		# Commit while locked: a concurrent runner must see the new requirements.
		frappe.db.commit()
		return created
	except Exception:
		frappe.db.rollback()
		raise
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock)
