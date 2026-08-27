"""Sync orphan purchase discounts into custom_discount_per.

The PO/PR/PI cost engines (PurchaseOrderCalculation, PRV1, pichatgpt) derive
net rate only from custom_vendor_rate and custom_discount_per. Discounts that
live only in ERPNext rate / discount_amount are wiped on the next validate —
notably when a Purchase Invoice is made from a Purchase Receipt.

This module backfills custom_discount_per before those engines run. Submitted
documents are left untouched. Historical invoices are not amended.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt


_RATE_EPS = 0.005


def _f(value) -> float:
	return flt(value)


def implied_discount_per(
	*,
	vendor_rate: float,
	rate: float = 0.0,
	discount_amount: float = 0.0,
	scheme_qty: float = 0.0,
	trade_offer_total: float = 0.0,
	fed_per: float = 0.0,
	fed_amount: float = 0.0,
) -> float:
	"""Return implied discount % when custom_discount_per is missing.

	Guards skip rows where scheme / trade offer / FED already explain a gap
	between vendor rate and net rate (those must not be converted into %).
	"""
	vendor_rate = _f(vendor_rate)
	if vendor_rate <= 0:
		return 0.0

	# Explicit ERPNext Discount Amount is the strongest orphan signal —
	# trust it even if scheme qty was autofilled (ACC-PINV-2026-00200).
	std_disc = _f(discount_amount)
	if std_disc > 0:
		return round((std_disc / vendor_rate) * 100.0, 6)

	# Rate-gap derivation only when scheme / trade offer / FED are not
	# already explaining a lower net unit cost.
	if _f(scheme_qty):
		return 0.0
	if _f(trade_offer_total):
		return 0.0
	if _f(fed_per) or _f(fed_amount):
		return 0.0

	rate = _f(rate)
	if rate > 0 and rate + _RATE_EPS < vendor_rate:
		return round(((vendor_rate - rate) / vendor_rate) * 100.0, 6)

	return 0.0


def _row_vendor_rate(row) -> float:
	return _f(row.get("custom_vendor_rate") or row.get("price_list_rate") or 0)


def _commercial_fields(doctype: str, name: str):
	return frappe.db.get_value(
		doctype,
		name,
		[
			"custom_discount_per",
			"custom_vendor_rate",
			"price_list_rate",
			"rate",
			"discount_amount",
			"custom_scheme_qty",
			"custom_trade_offer_total",
			"custom_fed_per",
			"custom_fed_amount",
		],
		as_dict=True,
	)


def apply_implied_discount_per(row, *, vendor_rate: float | None = None) -> float:
	"""Set row.custom_discount_per from local orphan rate/discount_amount."""
	existing = _f(row.get("custom_discount_per"))
	if existing:
		return existing

	vendor = _f(vendor_rate) if vendor_rate is not None else _row_vendor_rate(row)
	implied = implied_discount_per(
		vendor_rate=vendor,
		rate=_f(row.get("rate")),
		discount_amount=_f(row.get("discount_amount")),
		scheme_qty=_f(row.get("custom_scheme_qty")),
		trade_offer_total=_f(row.get("custom_trade_offer_total")),
		fed_per=_f(row.get("custom_fed_per")),
		fed_amount=_f(row.get("custom_fed_amount")),
	)
	if implied > 0:
		row.custom_discount_per = implied
	return implied


def apply_discount_per_from_source(row, source) -> float:
	"""Copy or derive custom_discount_per onto row from a PO/PR source row."""
	if not source:
		return apply_implied_discount_per(row)

	source_disc = _f(source.custom_discount_per)
	if source_disc > 0:
		row.custom_discount_per = source_disc
		return source_disc

	vendor = _f(source.custom_vendor_rate) or _f(source.price_list_rate)
	implied = implied_discount_per(
		vendor_rate=vendor,
		rate=_f(source.rate),
		discount_amount=_f(source.discount_amount),
		scheme_qty=_f(source.custom_scheme_qty),
		trade_offer_total=_f(source.custom_trade_offer_total),
		fed_per=_f(source.custom_fed_per),
		fed_amount=_f(source.custom_fed_amount),
	)
	if implied > 0:
		row.custom_discount_per = implied
		return implied

	return apply_implied_discount_per(row, vendor_rate=vendor or None)


def sync_purchase_order_discounts(doc, method=None):
	"""before_validate: backfill orphan discounts on draft Purchase Orders."""
	if getattr(doc, "docstatus", 0) != 0:
		return
	for row in doc.get("items") or []:
		apply_implied_discount_per(row)


def sync_purchase_receipt_discounts(doc, method=None):
	"""before_validate: backfill orphan discounts on draft Purchase Receipts."""
	if getattr(doc, "docstatus", 0) != 0:
		return
	for row in doc.get("items") or []:
		if apply_implied_discount_per(row):
			continue
		po_detail = row.get("purchase_order_item") or row.get("po_detail")
		if not po_detail:
			continue
		source = _commercial_fields("Purchase Order Item", po_detail)
		# Inherit only when PR still has no discount % of its own.
		if source and not _f(row.get("custom_discount_per")):
			apply_discount_per_from_source(row, source)


def sync_purchase_invoice_discounts(doc, method=None):
	"""before_validate: inherit PR discount / backfill orphans on draft PIs.

	PR-linked rows always take commercial discount from the PR line so a
	client preview that already wiped rate cannot lose the receipt discount.
	"""
	if getattr(doc, "docstatus", 0) != 0:
		return
	for row in doc.get("items") or []:
		pr_detail = row.get("pr_detail")
		if pr_detail:
			source = _commercial_fields("Purchase Receipt Item", pr_detail)
			apply_discount_per_from_source(row, source)
		else:
			apply_implied_discount_per(row)
