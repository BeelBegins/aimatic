"""Keep Update Stock on for Purchase Invoice returns.

Normal purchases use Purchase Receipt for stock (PI update_stock=0).
Standalone PI returns still need update_stock=1 or Bin never moves.

Repair of already-submitted returns (update_stock=0, no SLE) is cancel →
amend with update_stock=1 → resubmit. Live repair is opt-in only.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, cstr, flt


def ensure_purchase_invoice_return_updates_stock(doc, method=None):
	"""before_validate: default and lock Update Stock when Is Return is set."""
	if getattr(doc, "doctype", None) != "Purchase Invoice":
		return
	if cint(getattr(doc, "docstatus", 0)) != 0:
		return
	if not cint(getattr(doc, "is_return", 0)):
		return
	if cint(getattr(doc, "update_stock", 0)):
		return
	doc.update_stock = 1


def list_submitted_returns_without_stock_update(company: str | None = None) -> list[str]:
	filters = {
		"docstatus": 1,
		"is_return": 1,
		"update_stock": 0,
	}
	if company:
		filters["company"] = company
	return frappe.get_all(
		"Purchase Invoice",
		filters=filters,
		pluck="name",
		order_by="posting_date asc, name asc",
	)


def _stock_shortfalls(invoice_name: str) -> list[str]:
	items = frappe.db.sql(
		"""
		select idx, item_code, qty, stock_qty, conversion_factor, warehouse
		from `tabPurchase Invoice Item`
		where parent=%s
		order by idx
		""",
		invoice_name,
		as_dict=True,
	)
	issues: list[str] = []
	for it in items:
		if not it.warehouse:
			issues.append(f"r{it.idx} no warehouse")
			continue
		if not cint(frappe.db.get_value("Item", it.item_code, "is_stock_item")):
			continue
		need = abs(flt(it.stock_qty) or flt(it.qty) * flt(it.conversion_factor or 1))
		bin_qty = flt(
			frappe.db.get_value(
				"Bin",
				{"item_code": it.item_code, "warehouse": it.warehouse},
				"actual_qty",
			)
		)
		if bin_qty + 1e-9 < need:
			issues.append(
				f"r{it.idx} {it.item_code} need={need} have={bin_qty} @{it.warehouse}"
			)
	return issues


def dry_run_repair(company: str | None = None, names: list[str] | None = None) -> dict:
	"""Read-only feasibility for cancel → amend → resubmit repair."""
	if names is None:
		names = list_submitted_returns_without_stock_update(company=company)

	ok: list[dict] = []
	check: list[dict] = []
	for name in names:
		meta = frappe.db.get_value(
			"Purchase Invoice",
			name,
			[
				"name",
				"supplier",
				"posting_date",
				"grand_total",
				"docstatus",
				"is_return",
				"update_stock",
				"company",
			],
			as_dict=True,
		)
		if not meta:
			check.append({"name": name, "issues": ["missing document"]})
			continue
		if cint(meta.docstatus) != 1 or not cint(meta.is_return) or cint(meta.update_stock):
			check.append(
				{
					"name": name,
					"issues": [
						f"skip docstatus={meta.docstatus} is_return={meta.is_return} "
						f"update_stock={meta.update_stock}"
					],
				}
			)
			continue

		sle = frappe.db.count(
			"Stock Ledger Entry",
			{
				"voucher_type": "Purchase Invoice",
				"voucher_no": name,
				"is_cancelled": 0,
			},
		)
		pay = bool(
			frappe.db.exists(
				"Payment Entry Reference",
				{
					"reference_doctype": "Purchase Invoice",
					"reference_name": name,
					"docstatus": 1,
				},
			)
		)
		issues = _stock_shortfalls(name)
		if sle:
			issues.append(f"unexpected SLE count={sle}")
		if pay:
			issues.append("has submitted Payment Entry reference")

		row = {
			"name": name,
			"date": str(meta.posting_date),
			"supplier": meta.supplier,
			"grand_total": flt(meta.grand_total),
			"company": meta.company,
			"payment": pay,
			"sle": sle,
			"issues": issues,
		}
		(check if issues else ok).append(row)

	return {
		"total": len(names),
		"ok_n": len(ok),
		"check_n": len(check),
		"allow_negative_stock": cint(
			frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
		),
		"ok": ok,
		"check": check,
	}


def _prepare_amended_return(amended, *, original_name: str, posting_date, posting_time):
	amended.amended_from = original_name
	amended.update_stock = 1
	amended.set_posting_time = 1
	amended.posting_date = posting_date
	amended.posting_time = posting_time
	# Cancelled PI self-links in Tax Withholding Entry block amend insert.
	for row in amended.get("tax_withholding_entries") or []:
		if row.get("taxable_name") == original_name:
			row.taxable_name = None
		if row.get("withholding_name") == original_name:
			row.withholding_name = None
	# Older returns may predate Principal enforcement; fill first allowed if blank.
	if not (getattr(amended, "custom_principal", None) or "").strip():
		from aimatic.purchase_principal import get_allowed_principals

		allowed = get_allowed_principals(getattr(amended, "supplier", None))
		if allowed:
			amended.custom_principal = allowed[0]


def _relink_tax_withholding_entries(amended):
	for row in amended.get("tax_withholding_entries") or []:
		if row.get("taxable_doctype") == "Purchase Invoice" and not row.get("taxable_name"):
			row.taxable_name = amended.name
		if row.get("withholding_doctype") == "Purchase Invoice" and not row.get(
			"withholding_name"
		):
			row.withholding_name = amended.name


def _merge_duplicate_item_rows(doc) -> int:
	"""Collapse same item_code rows so Buying Settings multi-item check passes.

	Sums qty / stock_qty / amount-like fields onto the first row. Returns merges done.
	"""
	from collections import OrderedDict

	sum_fields = (
		"qty",
		"stock_qty",
		"received_qty",
		"rejected_qty",
		"amount",
		"base_amount",
		"net_amount",
		"base_net_amount",
		"custom_inventory_qty",
		"custom_scheme_qty",
		"custom_gross_total",
		"custom_gst_amount",
		"custom_advance_tax_amount",
		"custom_fed_amount",
		"custom_trade_offer_total",
		"custom_discount_amnt",
	)
	grouped: OrderedDict[str, object] = OrderedDict()
	merged = 0
	for row in list(doc.get("items") or []):
		key = cstr(row.item_code)
		if not key:
			continue
		if key not in grouped:
			grouped[key] = row
			continue
		keep = grouped[key]
		for field in sum_fields:
			if keep.meta.has_field(field) and row.meta.has_field(field):
				keep.set(field, flt(keep.get(field)) + flt(row.get(field)))
		doc.remove(row)
		merged += 1
	# reindex
	for idx, row in enumerate(doc.get("items") or [], start=1):
		row.idx = idx
	return merged


def repair_one_return(name: str, *, dry_run: bool = True) -> dict:
	"""Cancel submitted return, amend with update_stock=1, optionally submit."""
	doc = frappe.get_doc("Purchase Invoice", name)
	result = {
		"name": name,
		"dry_run": dry_run,
		"posting_date": str(doc.posting_date),
		"supplier": doc.supplier,
		"grand_total": flt(doc.grand_total),
	}

	if cint(doc.docstatus) != 1 or not cint(doc.is_return) or cint(doc.update_stock):
		result["status"] = "skipped"
		result["reason"] = (
			f"docstatus={doc.docstatus} is_return={doc.is_return} "
			f"update_stock={doc.update_stock}"
		)
		return result

	issues = _stock_shortfalls(name)
	result["shortfalls"] = issues

	if dry_run:
		result["status"] = "would_repair"
		result["plan"] = "cancel → amend update_stock=1 → submit (same posting date/time)"
		return result

	# Live path — caller must pass dry_run=False after backup + approval.
	frappe.flags.in_purchase_return_stock_repair = True
	try:
		posting_date = doc.posting_date
		posting_time = doc.posting_time
		doc.cancel()
		amended = frappe.copy_doc(doc)
		_prepare_amended_return(
			amended,
			original_name=name,
			posting_date=posting_date,
			posting_time=posting_time,
		)
		merged = _merge_duplicate_item_rows(amended)
		if merged:
			result["merged_duplicate_rows"] = merged
		amended.insert()
		_relink_tax_withholding_entries(amended)
		amended.save()
		amended.submit()
		result["status"] = "repaired"
		result["amended_name"] = amended.name
		result["sle"] = frappe.db.count(
			"Stock Ledger Entry",
			{
				"voucher_type": "Purchase Invoice",
				"voucher_no": amended.name,
				"is_cancelled": 0,
			},
		)
		return result
	except Exception as exc:
		frappe.db.rollback()
		result["status"] = "failed"
		result["error"] = str(exc)
		return result
	finally:
		frappe.flags.in_purchase_return_stock_repair = False


def repair_returns(
	names: list[str] | None = None,
	*,
	company: str | None = None,
	dry_run: bool = True,
	only_ok: bool = True,
	limit: int | None = None,
) -> dict:
	"""Batch repair. Default dry_run=True and only invoices with no shortfall flags."""
	report = dry_run_repair(company=company, names=names)
	targets = list(report["ok"]) if only_ok else list(report["ok"]) + list(report["check"])
	if limit is not None:
		targets = targets[:limit]

	results = []
	for row in targets:
		try:
			results.append(repair_one_return(row["name"], dry_run=dry_run))
			if not dry_run and results[-1].get("status") == "repaired":
				frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			results.append(
				{
					"name": row["name"],
					"dry_run": dry_run,
					"status": "failed",
					"error": str(exc),
				}
			)

	repaired = sum(1 for r in results if r.get("status") == "repaired")
	failed = sum(1 for r in results if r.get("status") == "failed")
	return {
		"dry_run": dry_run,
		"only_ok": only_ok,
		"planned": len(targets),
		"repaired": repaired,
		"failed": failed,
		"feasibility": {
			"total": report["total"],
			"ok_n": report["ok_n"],
			"check_n": report["check_n"],
			"allow_negative_stock": report["allow_negative_stock"],
			"check": report["check"],
		},
		"results": results,
	}
