"""Propose and create draft Principal Historical Allocations for a supplier.

Inference is advisory only: drafts are never submitted. Finance must review
evidence notes before submit. Conflicting or empty evidence is skipped.
"""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from aimatic.aimatic.doctype.principal_historical_allocation.principal_historical_allocation import (
	SOURCE_META,
)

VOUCHER_CHILD = {
	"Purchase Invoice": ("Purchase Invoice Item", "posting_date", "base_net_amount", "amount"),
	"Purchase Receipt": ("Purchase Receipt Item", "posting_date", "base_net_amount", "amount"),
	"Purchase Order": ("Purchase Order Item", "transaction_date", "base_net_amount", "amount"),
}

CONFIDENT_STATUSES = frozenset({"Approved Item Mapping", "Unique Later Evidence"})


@frappe.whitelist()
def propose_supplier_historical_allocations(
	supplier: str,
	company: str | None = None,
	voucher_type: str = "Purchase Invoice",
	from_date=None,
	to_date=None,
	include_tagged: int | bool = 0,
	limit: int = 200,
):
	"""Return draft proposals for one supplier. Does not write documents."""
	supplier, company, voucher_type = _resolve_scope(supplier, company, voucher_type)
	limit = max(1, min(cint(limit or 200), 500))
	proposals = _build_proposals(
		supplier=supplier,
		company=company,
		voucher_type=voucher_type,
		from_date=from_date,
		to_date=to_date,
		include_tagged=bool(cint(include_tagged)),
		limit=limit,
	)
	return {
		"supplier": supplier,
		"company": company,
		"voucher_type": voucher_type,
		"ready_count": sum(1 for row in proposals if row["status"] == "ready"),
		"skipped_count": sum(1 for row in proposals if row["status"] != "ready"),
		"proposals": proposals,
	}


@frappe.whitelist()
def create_supplier_historical_allocation_drafts(
	supplier: str,
	company: str | None = None,
	voucher_type: str = "Purchase Invoice",
	from_date=None,
	to_date=None,
	include_tagged: int | bool = 0,
	limit: int = 200,
):
	"""Create draft Principal Historical Allocation docs for confident proposals.

	Never submits. Skips vouchers that already have a draft or submitted allocation.
	"""
	payload = propose_supplier_historical_allocations(
		supplier=supplier,
		company=company,
		voucher_type=voucher_type,
		from_date=from_date,
		to_date=to_date,
		include_tagged=include_tagged,
		limit=limit,
	)
	created = []
	skipped = []
	for proposal in payload["proposals"]:
		if proposal["status"] != "ready":
			skipped.append(
				{
					"voucher_no": proposal["voucher_no"],
					"reason": proposal.get("skip_reason") or proposal["status"],
				}
			)
			continue
		if frappe.db.exists(
			"Principal Historical Allocation",
			{"voucher_type": proposal["voucher_type"], "voucher_no": proposal["voucher_no"]},
		):
			skipped.append({"voucher_no": proposal["voucher_no"], "reason": "allocation_exists"})
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Principal Historical Allocation",
				"voucher_type": proposal["voucher_type"],
				"voucher_no": proposal["voucher_no"],
				"source": proposal["source"],
				"evidence_notes": proposal["evidence_notes"],
				"allocations": proposal["allocations"],
			}
		)
		doc.insert()
		created.append({"name": doc.name, "voucher_no": doc.voucher_no, "allocated_total": doc.allocated_total})

	return {
		"supplier": payload["supplier"],
		"company": payload["company"],
		"voucher_type": payload["voucher_type"],
		"created_count": len(created),
		"skipped_count": len(skipped),
		"created": created,
		"skipped": skipped,
	}


@frappe.whitelist()
def run_allocation_draft_bot(
	company: str | None = None,
	voucher_type: str = "Purchase Invoice",
	from_date=None,
	to_date=None,
	limit_per_supplier: int = 200,
	supplier_limit: int = 500,
	dry_run: int | bool = 0,
	enqueue: int | bool = 0,
):
	"""Auto-script / bot: create draft allocations for every eligible vendor.

	Never submits. Unreviewed drafts stay draft and do not affect Principal
	reports until someone reviews and submits them.
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	if not frappe.has_permission("Principal Historical Allocation", ptype="create"):
		frappe.throw(_("Not permitted to create Principal Historical Allocation."), frappe.PermissionError)

	company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company:
		frappe.throw(_("Company is required."))
	if voucher_type not in SOURCE_META:
		frappe.throw(_("Unsupported voucher type {0}").format(frappe.bold(voucher_type)))

	dry_run = bool(cint(dry_run))
	if cint(enqueue) and not dry_run:
		frappe.enqueue(
			"aimatic.principal_allocation_automation.run_allocation_draft_bot",
			queue="long",
			timeout=3600,
			company=company,
			voucher_type=voucher_type,
			from_date=from_date,
			to_date=to_date,
			limit_per_supplier=limit_per_supplier,
			supplier_limit=supplier_limit,
			dry_run=0,
			enqueue=0,
		)
		return {
			"enqueued": 1,
			"company": company,
			"voucher_type": voucher_type,
			"message": _("Principal allocation draft bot queued. Drafts only — nothing is submitted."),
		}

	suppliers = _suppliers_needing_allocation(
		company=company,
		voucher_type=voucher_type,
		from_date=from_date,
		to_date=to_date,
		limit=max(1, min(cint(supplier_limit or 500), 2000)),
	)
	limit_per_supplier = max(1, min(cint(limit_per_supplier or 200), 500))

	supplier_results = []
	created_total = 0
	skipped_total = 0
	ready_total = 0

	for supplier in suppliers:
		if dry_run:
			payload = propose_supplier_historical_allocations(
				supplier=supplier,
				company=company,
				voucher_type=voucher_type,
				from_date=from_date,
				to_date=to_date,
				limit=limit_per_supplier,
			)
			ready = cint(payload.get("ready_count"))
			skipped = cint(payload.get("skipped_count"))
			created = 0
		else:
			payload = create_supplier_historical_allocation_drafts(
				supplier=supplier,
				company=company,
				voucher_type=voucher_type,
				from_date=from_date,
				to_date=to_date,
				limit=limit_per_supplier,
			)
			created = cint(payload.get("created_count"))
			skipped = cint(payload.get("skipped_count"))
			ready = created
		created_total += created
		skipped_total += skipped
		ready_total += ready
		if created or ready:
			supplier_results.append(
				{
					"supplier": supplier,
					"created_count": created,
					"ready_count": ready,
					"skipped_count": skipped,
				}
			)

	summary = {
		"company": company,
		"voucher_type": voucher_type,
		"dry_run": int(dry_run),
		"supplier_count": len(suppliers),
		"suppliers_with_work": len(supplier_results),
		"created_count": created_total,
		"ready_count": ready_total,
		"skipped_count": skipped_total,
		"supplier_results": supplier_results,
		"message": _(
			"Drafts only. Review and submit what you accept; leave the rest as draft — reports ignore unsubmitted allocations."
		),
	}
	frappe.logger("principal_allocation").info(summary)
	return summary


def run_daily_allocation_draft_bot():
	"""Scheduler entry: auto-apply Principals site-wide when enabled.

	Enable with site_config ``principal_allocation_auto_drafts: 1``.
	Seeds allow-lists and tags PR/PI/Items — no human review.
	"""
	if not frappe.conf.get("principal_allocation_auto_drafts"):
		return
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	return auto_apply_principals(
		company=company,
		dry_run=0,
		update_items=1,
		aggressive=1,
		enqueue=0,
	)


def _suppliers_needing_allocation(
	*,
	company: str,
	voucher_type: str,
	from_date=None,
	to_date=None,
	limit: int = 500,
) -> list[str]:
	date_field, _total_field = SOURCE_META[voucher_type]
	conditions = [
		"v.docstatus = 1",
		"v.company = %(company)s",
		"IFNULL(v.custom_principal, '') = ''",
	]
	values = {"company": company, "limit": limit}
	if voucher_type != "Purchase Order":
		conditions.append("IFNULL(v.is_return, 0) = 0")
	if from_date:
		conditions.append(f"v.{date_field} >= %(from_date)s")
		values["from_date"] = getdate(from_date)
	if to_date:
		conditions.append(f"v.{date_field} <= %(to_date)s")
		values["to_date"] = getdate(to_date)

	rows = frappe.db.sql(
		f"""
		SELECT DISTINCT v.supplier
		FROM `tab{voucher_type}` v
		WHERE {" AND ".join(conditions)}
		  AND NOT EXISTS (
			SELECT 1
			FROM `tabPrincipal Historical Allocation` pha
			WHERE pha.voucher_type = %(voucher_type)s
			  AND pha.voucher_no = v.name
		  )
		ORDER BY v.supplier
		LIMIT %(limit)s
		""",
		{**values, "voucher_type": voucher_type},
		as_dict=True,
	)
	return [row.supplier for row in rows if row.supplier]


def _resolve_scope(supplier: str, company: str | None, voucher_type: str):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	if not supplier:
		frappe.throw(_("Supplier is required."))
	if not frappe.has_permission("Supplier", ptype="read", doc=supplier):
		frappe.throw(_("Not permitted to view this supplier."), frappe.PermissionError)
	if not frappe.has_permission("Principal Historical Allocation", ptype="create"):
		frappe.throw(_("Not permitted to create Principal Historical Allocation."), frappe.PermissionError)
	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		frappe.throw(_("Company is required."))
	if voucher_type not in SOURCE_META:
		frappe.throw(_("Unsupported voucher type {0}").format(frappe.bold(voucher_type)))
	return supplier, company, voucher_type


def _build_proposals(
	*,
	supplier: str,
	company: str,
	voucher_type: str,
	from_date,
	to_date,
	include_tagged: bool,
	limit: int,
):
	vouchers = _candidate_vouchers(
		supplier=supplier,
		company=company,
		voucher_type=voucher_type,
		from_date=from_date,
		to_date=to_date,
		include_tagged=include_tagged,
		limit=limit,
	)
	if not vouchers:
		return []

	already = set(
		frappe.get_all(
			"Principal Historical Allocation",
			filters={
				"voucher_type": voucher_type,
				"voucher_no": ["in", [row.name for row in vouchers]],
			},
			pluck="voucher_no",
		)
	)
	lines_by_voucher = _voucher_lines(voucher_type, [row.name for row in vouchers])
	item_codes = sorted({line.item_code for lines in lines_by_voucher.values() for line in lines if line.item_code})
	item_principals = _item_principals(item_codes)
	evidence = _later_principal_evidence(company=company, supplier=supplier, item_codes=item_codes)

	proposals = []
	for voucher in vouchers:
		if voucher.name in already:
			proposals.append(
				{
					"voucher_type": voucher_type,
					"voucher_no": voucher.name,
					"posting_date": str(voucher.posting_date) if voucher.posting_date else None,
					"document_total": flt(voucher.document_total),
					"status": "skipped",
					"skip_reason": "allocation_exists",
					"allocations": [],
					"source": "Later Tagged Document",
					"evidence_notes": "",
				}
			)
			continue
		proposals.append(
			_propose_one_voucher(
				voucher_type=voucher_type,
				voucher=voucher,
				lines=lines_by_voucher.get(voucher.name) or [],
				item_principals=item_principals,
				evidence=evidence,
			)
		)
	return proposals


def _candidate_vouchers(
	*,
	supplier: str,
	company: str,
	voucher_type: str,
	from_date,
	to_date,
	include_tagged: bool,
	limit: int,
):
	date_field, total_field = SOURCE_META[voucher_type]
	conditions = [
		"docstatus = 1",
		"company = %(company)s",
		"supplier = %(supplier)s",
	]
	values = {"company": company, "supplier": supplier, "limit": limit}
	if voucher_type != "Purchase Order":
		conditions.append("IFNULL(is_return, 0) = 0")
	if not include_tagged:
		conditions.append("IFNULL(custom_principal, '') = ''")
	if from_date:
		conditions.append(f"{date_field} >= %(from_date)s")
		values["from_date"] = getdate(from_date)
	if to_date:
		conditions.append(f"{date_field} <= %(to_date)s")
		values["to_date"] = getdate(to_date)

	return frappe.db.sql(
		f"""
		SELECT
			name,
			{date_field} AS posting_date,
			IFNULL(custom_principal, '') AS document_principal,
			COALESCE({total_field}, grand_total, 0) AS document_total
		FROM `tab{voucher_type}`
		WHERE {" AND ".join(conditions)}
		ORDER BY {date_field} ASC, name ASC
		LIMIT %(limit)s
		""",
		values,
		as_dict=True,
	)


def _voucher_lines(voucher_type: str, voucher_names: list[str]) -> dict[str, list]:
	child, _date_field, preferred_amount, fallback_amount = VOUCHER_CHILD[voucher_type]
	if not voucher_names:
		return {}
	rows = frappe.db.sql(
		f"""
		SELECT
			name AS source_row,
			parent AS voucher_no,
			item_code,
			COALESCE({preferred_amount}, {fallback_amount}, 0) AS line_amount
		FROM `tab{child}`
		WHERE parent IN %(voucher_names)s
		  AND IFNULL(item_code, '') != ''
		ORDER BY idx ASC, name ASC
		""",
		{"voucher_names": tuple(voucher_names)},
		as_dict=True,
	)
	grouped = defaultdict(list)
	for row in rows:
		grouped[row.voucher_no].append(row)
	return dict(grouped)


def _item_principals(item_codes: list[str]) -> dict[str, str]:
	if not item_codes:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT name, IFNULL(custom_principal, '') AS custom_principal
		FROM `tabItem`
		WHERE name IN %(item_codes)s
		""",
		{"item_codes": tuple(item_codes)},
		as_dict=True,
	)
	return {row.name: row.custom_principal for row in rows if row.custom_principal}


def _later_principal_evidence(company: str, supplier: str, item_codes: list[str]) -> dict[str, list]:
	"""Map item_code -> list of (posting_date, principal, voucher_no) from later tagged docs."""
	if not item_codes:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT pri.item_code, pr.posting_date, pr.custom_principal AS principal, pr.name AS voucher_no
		FROM `tabPurchase Receipt` pr
		JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
		WHERE pr.company = %(company)s
		  AND pr.supplier = %(supplier)s
		  AND pr.docstatus = 1
		  AND IFNULL(pr.custom_principal, '') != ''
		  AND pri.item_code IN %(item_codes)s

		UNION ALL

		SELECT pii.item_code, pi.posting_date, pi.custom_principal, pi.name
		FROM `tabPurchase Invoice` pi
		JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
		WHERE pi.company = %(company)s
		  AND pi.supplier = %(supplier)s
		  AND pi.docstatus = 1
		  AND IFNULL(pi.custom_principal, '') != ''
		  AND pii.item_code IN %(item_codes)s
		""",
		{"company": company, "supplier": supplier, "item_codes": tuple(item_codes)},
		as_dict=True,
	)
	grouped = defaultdict(list)
	for row in rows:
		grouped[row.item_code].append(row)
	return dict(grouped)


def _resolve_line_principal(item_code: str, posting_date, item_principals: dict, evidence: dict):
	mapped = item_principals.get(item_code)
	if mapped:
		return mapped, "Approved Item Mapping", []

	on_or_after = getdate(posting_date) if posting_date else None
	later = []
	for row in evidence.get(item_code) or []:
		if on_or_after and row.posting_date and getdate(row.posting_date) < on_or_after:
			continue
		if row.principal:
			later.append(row)
	principals = sorted({row.principal for row in later})
	if len(principals) == 1:
		return principals[0], "Unique Later Evidence", later
	if len(principals) > 1:
		return None, "Conflicting Later Evidence", later
	return None, "No Later Evidence", later


def _propose_one_voucher(*, voucher_type, voucher, lines, item_principals, evidence):
	base = {
		"voucher_type": voucher_type,
		"voucher_no": voucher.name,
		"posting_date": str(voucher.posting_date) if voucher.posting_date else None,
		"document_total": flt(voucher.document_total),
		"allocations": [],
		"source": "Later Tagged Document",
		"evidence_notes": "",
	}
	if not lines:
		return {**base, "status": "skipped", "skip_reason": "no_item_lines"}

	amounts = defaultdict(float)
	evidence_vouchers = set()
	statuses = set()
	for line in lines:
		principal, status, later_rows = _resolve_line_principal(
			line.item_code, voucher.posting_date, item_principals, evidence
		)
		statuses.add(status)
		if status not in CONFIDENT_STATUSES or not principal:
			return {
				**base,
				"status": "skipped",
				"skip_reason": status,
			}
		amounts[principal] += flt(line.line_amount)
		for row in later_rows:
			evidence_vouchers.add(row.voucher_no)

	line_total = sum(amounts.values())
	document_total = flt(voucher.document_total)
	if not line_total or not document_total:
		return {**base, "status": "skipped", "skip_reason": "zero_total"}

	# Scale line-basis amounts onto document total so PHA validation passes.
	scaled = []
	running = 0.0
	principals = sorted(amounts.keys())
	for index, principal in enumerate(principals):
		if index == len(principals) - 1:
			value = round(flt(document_total) - running, 2)
		else:
			value = round(flt(document_total) * (amounts[principal] / line_total), 2)
			running += value
		scaled.append(
			{
				"principal": principal,
				"allocated_amount": value,
			}
		)

	source = "Later Tagged Document" if "Unique Later Evidence" in statuses else "Manual Review"

	notes = _(
		"Auto-proposed draft for {0}. Line classification: {1}. "
		"Evidence vouchers: {2}. "
		"Optional review: submit if accepted; leave as draft if not — reports ignore drafts."
	).format(
		voucher.name,
		", ".join(sorted(statuses)),
		", ".join(sorted(evidence_vouchers)) if evidence_vouchers else _("none (item mapping only)"),
	)

	return {
		**base,
		"status": "ready",
		"skip_reason": "",
		"allocations": scaled,
		"source": source,
		"evidence_notes": notes,
	}



@frappe.whitelist()
def backfill_principals_from_receipts(
	company: str | None = None,
	dry_run: int | bool = 0,
	update_items: int | bool = 1,
	enqueue: int | bool = 0,
	aggressive: int | bool = 1,
):
	"""Compatibility wrapper — runs full auto-apply (no human review)."""
	return auto_apply_principals(
		company=company,
		dry_run=dry_run,
		update_items=update_items,
		enqueue=enqueue,
		aggressive=aggressive,
	)


@frappe.whitelist()
def auto_apply_principals(
	company: str | None = None,
	dry_run: int | bool = 0,
	update_items: int | bool = 1,
	enqueue: int | bool = 0,
	aggressive: int | bool = 1,
):
	"""Fill Principals end-to-end without review.

	1. Seed Supplier.custom_principals from trailing ``(BRAND)`` when Principal exists
	   (skips internal SIEZAL SUPERMARKET branch parties)
	2. Tag blank submitted PR/PI via sole allow-list, name hint, item map/history,
	   then majority (>=80% lines same mapped Principal)
	3. Copy unique PR Principal onto blank linked PIs (no PR → leave blank)
	4. Map blank Item.custom_principal from unique tagged evidence
	5. Repeat PR/PI/Item once more for newly unlocked rows

	Writes with ``frappe.db.set_value`` / Supplier save. Does not invent mixed
	multi-brand documents — those stay blank (payable split still needs PHA).
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	if not (
		frappe.has_permission("Purchase Invoice", ptype="write")
		or frappe.has_permission("Purchase Receipt", ptype="write")
		or frappe.has_permission("Supplier", ptype="write")
	):
		frappe.throw(_("Not permitted to auto-apply Principals."), frappe.PermissionError)

	company = company or frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company:
		frappe.throw(_("Company is required."))

	dry_run = bool(cint(dry_run))
	update_items = bool(cint(update_items))
	aggressive = bool(cint(aggressive))
	if cint(enqueue) and not dry_run:
		frappe.enqueue(
			"aimatic.principal_allocation_automation.auto_apply_principals",
			queue="long",
			timeout=3600,
			company=company,
			dry_run=0,
			update_items=int(update_items),
			aggressive=int(aggressive),
			enqueue=0,
		)
		return {
			"enqueued": 1,
			"company": company,
			"message": _("Principal auto-apply queued (seed allow-list → tag PR/PI → map Items)."),
		}

	from aimatic.purchase_principal import (
		ensure_supplier_principal,
		extract_principal_from_supplier_name,
		get_allowed_principals,
		guess_principal_from_items,
		is_internal_supplier,
	)

	seeded = []
	allowed_overlay: dict[str, list[str]] = {}
	if aggressive:
		suppliers = frappe.db.sql(
			"""
			SELECT DISTINCT supplier FROM (
				SELECT supplier FROM `tabPurchase Receipt`
				WHERE docstatus = 1 AND company = %(company)s
				  AND IFNULL(is_return, 0) = 0 AND IFNULL(custom_principal, '') = ''
				UNION
				SELECT supplier FROM `tabPurchase Invoice`
				WHERE docstatus = 1 AND company = %(company)s
				  AND IFNULL(is_return, 0) = 0 AND IFNULL(custom_principal, '') = ''
			) blank_suppliers
			""",
			{"company": company},
			as_dict=True,
		)
		for row in suppliers:
			supplier = row.supplier
			if is_internal_supplier(supplier):
				continue
			hint = extract_principal_from_supplier_name(supplier)
			if not hint:
				continue
			allowed = get_allowed_principals(supplier)
			if hint in allowed:
				allowed_overlay[supplier] = allowed
				continue
			seeded.append({"supplier": supplier, "principal": hint})
			allowed_overlay[supplier] = list(allowed) + [hint]
			if not dry_run:
				ensure_supplier_principal(supplier, hint)

	def _allowed_for(supplier: str) -> list[str]:
		if supplier in allowed_overlay:
			return allowed_overlay[supplier]
		return get_allowed_principals(supplier)

	def _unique_item_evidence():
		return frappe.db.sql(
			"""
			SELECT item_code, MIN(principal) AS principal
			FROM (
				SELECT pri.item_code, pr.custom_principal AS principal
				FROM `tabPurchase Receipt` pr
				JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
				WHERE pr.company = %(company)s
				  AND pr.docstatus = 1
				  AND IFNULL(pr.custom_principal, '') != ''
				  AND IFNULL(pri.item_code, '') != ''
				UNION ALL
				SELECT pii.item_code, pi.custom_principal
				FROM `tabPurchase Invoice` pi
				JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
				WHERE pi.company = %(company)s
				  AND pi.docstatus = 1
				  AND IFNULL(pi.custom_principal, '') != ''
				  AND IFNULL(pii.item_code, '') != ''
			) evidence
			GROUP BY item_code
			HAVING COUNT(DISTINCT principal) = 1
			""",
			{"company": company},
			as_dict=True,
		)

	def _apply_item_mappings(rows):
		updated = []
		already = 0
		for row in rows:
			current = frappe.db.get_value("Item", row.item_code, "custom_principal")
			if current:
				already += 1
				continue
			updated.append({"item_code": row.item_code, "principal": row.principal})
			if not dry_run:
				frappe.db.set_value(
					"Item", row.item_code, "custom_principal", row.principal, update_modified=False
				)
		return updated, already

	def _majority_item_principal(doctype: str, name: str, supplier: str) -> str | None:
		child = "Purchase Receipt Item" if doctype == "Purchase Receipt" else "Purchase Invoice Item"
		rows = frappe.db.sql(
			f"""
			SELECT IFNULL(i.custom_principal, '') AS principal, COUNT(*) AS line_count
			FROM `tab{child}` c
			LEFT JOIN `tabItem` i ON i.name = c.item_code
			WHERE c.parent = %(name)s AND IFNULL(c.item_code, '') != ''
			GROUP BY IFNULL(i.custom_principal, '')
			""",
			{"name": name},
			as_dict=True,
		)
		total = sum(int(r.line_count) for r in rows) or 0
		if total < 1:
			return None
		mapped = [r for r in rows if r.principal]
		if not mapped:
			return None
		top = max(mapped, key=lambda r: int(r.line_count))
		if int(top.line_count) / total < 0.8:
			return None
		if len([r for r in mapped if int(r.line_count) == int(top.line_count)]) > 1:
			return None
		allowed = _allowed_for(supplier)
		if allowed and top.principal not in allowed:
			return None
		if not allowed:
			return None
		return top.principal

	def _resolve_doc_principal(doctype: str, name: str, supplier: str, company_name: str, items):
		allowed = _allowed_for(supplier)
		# Sole / name hint from overlay (works in dry-run before DB seed).
		if len(allowed) == 1:
			return allowed[0]
		hint = extract_principal_from_supplier_name(supplier)
		if hint and hint in allowed:
			return hint
		guess_doc = SimpleNamespace(
			supplier=supplier,
			company=company_name,
			items=[SimpleNamespace(item_code=row.item_code) for row in items if row.item_code],
		)
		principal = guess_principal_from_items(guess_doc)
		if principal and (not allowed or principal in allowed):
			return principal
		if aggressive:
			principal = _majority_item_principal(doctype, name, supplier)
			if principal:
				return principal
		return None

	item_updated = []
	item_skipped = 0
	if update_items:
		first_items, item_skipped = _apply_item_mappings(_unique_item_evidence())
		item_updated.extend(first_items)

	pr_updated = []
	pr_skipped = 0
	pr_overlay = {}
	pi_updated = []
	pi_skipped_no_pr = 0
	pi_skipped_blank_pr = 0
	pi_skipped_conflict = 0
	pi_skipped_other = 0

	for _pass in range(2):
		blank_prs = frappe.db.sql(
			"""
			SELECT name, supplier, company
			FROM `tabPurchase Receipt`
			WHERE docstatus = 1 AND company = %(company)s
			  AND IFNULL(is_return, 0) = 0 AND IFNULL(custom_principal, '') = ''
			ORDER BY posting_date ASC, name ASC
			""",
			{"company": company},
			as_dict=True,
		)
		for pr in blank_prs:
			if pr.name in pr_overlay:
				continue
			items = frappe.get_all(
				"Purchase Receipt Item", filters={"parent": pr.name}, fields=["item_code"]
			)
			principal = _resolve_doc_principal(
				"Purchase Receipt", pr.name, pr.supplier, pr.company or company, items
			)
			if not principal:
				if _pass == 1:
					pr_skipped += 1
				continue
			pr_updated.append(
				{"name": pr.name, "supplier": pr.supplier, "principal": principal, "source": "auto"}
			)
			pr_overlay[pr.name] = principal
			if not dry_run:
				frappe.db.set_value(
					"Purchase Receipt", pr.name, "custom_principal", principal, update_modified=False
				)

		blank_pis = frappe.db.sql(
			"""
			SELECT name, supplier, company
			FROM `tabPurchase Invoice`
			WHERE docstatus = 1 AND company = %(company)s
			  AND IFNULL(is_return, 0) = 0 AND IFNULL(custom_principal, '') = ''
			ORDER BY posting_date ASC, name ASC
			""",
			{"company": company},
			as_dict=True,
		)
		seen_pi = {row["name"] for row in pi_updated}
		for pi in blank_pis:
			if pi.name in seen_pi:
				continue
			pr_rows = frappe.db.sql(
				"""
				SELECT DISTINCT pr.name, IFNULL(pr.custom_principal, '') AS principal
				FROM `tabPurchase Invoice Item` pii
				JOIN `tabPurchase Receipt` pr ON pr.name = pii.purchase_receipt
				WHERE pii.parent = %(pi)s
				  AND IFNULL(pii.purchase_receipt, '') != ''
				  AND pr.docstatus = 1
				""",
				{"pi": pi.name},
				as_dict=True,
			)
			principal = None
			if pr_rows:
				principals = sorted(
					{
						pr_overlay.get(row.name) or row.principal
						for row in pr_rows
						if (pr_overlay.get(row.name) or row.principal)
					}
				)
				if len(principals) == 1:
					principal = principals[0]
				elif len(principals) > 1:
					if _pass == 1:
						pi_skipped_conflict += 1
					continue
				elif _pass == 1:
					pi_skipped_blank_pr += 1
			elif _pass == 1:
				pi_skipped_no_pr += 1

			if not principal:
				items = frappe.get_all(
					"Purchase Invoice Item", filters={"parent": pi.name}, fields=["item_code"]
				)
				principal = _resolve_doc_principal(
					"Purchase Invoice", pi.name, pi.supplier, pi.company or company, items
				)

			if not principal:
				if _pass == 1 and not pr_rows:
					pass  # already counted no_pr
				elif _pass == 1 and pr_rows and not (pr_overlay or True):
					pi_skipped_other += 1
				continue

			allowed = _allowed_for(pi.supplier)
			if allowed and principal not in allowed:
				if _pass == 1:
					pi_skipped_conflict += 1
				continue
			if not allowed:
				if _pass == 1:
					pi_skipped_other += 1
				continue

			pi_updated.append({"name": pi.name, "supplier": pi.supplier, "principal": principal})
			seen_pi.add(pi.name)
			if not dry_run:
				frappe.db.set_value(
					"Purchase Invoice", pi.name, "custom_principal", principal, update_modified=False
				)

		if update_items:
			more_items, _already = _apply_item_mappings(_unique_item_evidence())
			seen = {row["item_code"] for row in item_updated}
			for row in more_items:
				if row["item_code"] not in seen:
					item_updated.append(row)

	# Deduplicate PR list while preserving order
	dedup_pr = []
	seen_pr = set()
	for row in pr_updated:
		if row["name"] in seen_pr:
			continue
		seen_pr.add(row["name"])
		dedup_pr.append(row)
	pr_updated = dedup_pr

	dedup_pi = []
	seen_pi2 = set()
	for row in pi_updated:
		if row["name"] in seen_pi2:
			continue
		seen_pi2.add(row["name"])
		dedup_pi.append(row)
	pi_updated = dedup_pi

	if not dry_run:
		frappe.db.commit()

	summary = {
		"company": company,
		"dry_run": int(dry_run),
		"aggressive": int(aggressive),
		"supplier_principals_seeded": len(seeded),
		"seed_samples": seeded[:20],
		"purchase_receipts_updated": len(pr_updated),
		"purchase_receipts_skipped": pr_skipped,
		"purchase_invoices_updated": len(pi_updated),
		"purchase_invoices_skipped_no_pr": pi_skipped_no_pr,
		"purchase_invoices_skipped_blank_pr": pi_skipped_blank_pr,
		"purchase_invoices_skipped_conflict": pi_skipped_conflict,
		"purchase_invoices_skipped_other": pi_skipped_other,
		"items_updated": len(item_updated),
		"items_already_mapped": item_skipped,
		"receipt_samples": pr_updated[:20],
		"invoice_samples": pi_updated[:20],
		"item_samples": item_updated[:20],
		"message": _(
			"Auto-apply complete (no review). Seeded supplier allow-lists from name hints; "
			"tagged PR/PI from sole/hint/items/majority; copied PR→PI when unique; "
			"mapped Items from unique evidence. Mixed multi-brand docs left blank."
		),
	}
	frappe.logger("principal_allocation").info(summary)
	return summary
