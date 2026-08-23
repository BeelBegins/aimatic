from __future__ import annotations

from collections import defaultdict
from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, today

# Replaying stock ledger FIFO layers for exact vendor-origin stock is O(all-time
# ledger entries) for the linked item set. Past this many rows it's too slow to
# compute inline on a drill-through click, so the caller is told to narrow down
# instead of the request hanging.
_MAX_ORIGIN_STOCK_SLE_ROWS = 150_000

# Trailing window used to establish each item's own "normal" daily sell-through
# pace, independent of the (shorter) evaluation window used elsewhere on this page.
# Deliberately long and fixed so a single recent purchase/sale spike can't skew an
# item's own baseline.
_VELOCITY_BASELINE_DAYS = 180

# Below this much total sold quantity in the baseline window, a per-item daily rate
# is too noisy to trust (e.g. an item sold twice in 180 days doesn't have a
# meaningful "pace") - falls back to peer comparison instead.
_VELOCITY_MIN_BASELINE_SOLD_QTY = 3

# One-off, szl-only historical correction (2026-07-11): these Stock Entries backfill
# the stock/COGS impact of POS Invoices submitted with update_stock=0 before that bug
# was fixed in offline_pos._build_pos_invoice_doc. The originals were deliberately never
# touched (already FBR-reported), so their cost never lands under voucher_type in
# ('Sales Invoice', 'POS Invoice') like a normal sale's SLE would. Counting these
# specific, known documents as COGS too keeps this dashboard's numbers reconciled for
# that historical window without misclassifying unrelated Material Issues (write-offs,
# internal consumption, etc.) as sales COGS. See CLAUDE.md's offline_pos section.
_HISTORICAL_POS_STOCK_CORRECTION_ENTRIES = (
	"MAT-STE-2026-00008",
	"MAT-STE-2026-00009",
	"MAT-STE-2026-00010",
	"MAT-STE-2026-00011",
	"MAT-STE-2026-00012",
	"MAT-STE-2026-00013",
	"MAT-STE-2026-00014",
	"MAT-STE-2026-00015",
	"MAT-STE-2026-00016",
	"MAT-STE-2026-00017",
)


def _resolve_context(supplier: str, company: str | None):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))

	if not supplier:
		frappe.throw(_("Supplier is required."))

	company = company or frappe.defaults.get_user_default("Company")
	if not company:
		frappe.throw(_("Company is required."))

	if not frappe.has_permission("Supplier", ptype="read", doc=supplier):
		frappe.throw(_("Not permitted to view this supplier."), frappe.PermissionError)

	supplier_meta = frappe.db.get_value(
		"Supplier",
		supplier,
		["supplier_name", "supplier_group"],
		as_dict=True,
	)
	if not supplier_meta:
		frappe.throw(_("Supplier {0} was not found.").format(frappe.bold(supplier)))

	currency = frappe.db.get_value("Company", company, "default_currency")
	return supplier, company, supplier_meta, currency


def _get_date_window(lookback_days: int):
	lookback_days = max(1, min(cint(lookback_days or 30), 365))
	date_to = getdate(today())
	date_from = getdate(add_days(date_to, -(lookback_days - 1)))
	return lookback_days, date_from, date_to


def _resolve_branch_warehouse_scope(
	branch: str | None, warehouse: str | None
) -> tuple[str | None, list[str] | None]:
	"""Single source of truth for the two optional scope filters.

	warehouse given -> ([warehouse], that warehouse's own custom_branch - may be
	None if untagged, in which case header-level branch filtering is skipped for
	that call only, matching today's unfiltered behavior for that one dimension).

	branch given (no warehouse) -> (branch, its warehouse list, or None - not [] -
	if it resolves to zero warehouses, so downstream `if warehouse_list:` checks
	skip the SLE/Bin filter entirely instead of building `WHERE warehouse IN ()`
	and zeroing every stock/COGS metric).

	Neither given -> (None, None), identical to today's unfiltered behavior.

	Warehouse always wins over a simultaneously-set Branch (derived, never
	validated/rejected) so the UI can't produce a contradictory combination that
	silently disagrees between filters.
	"""
	if warehouse:
		return frappe.db.get_value("Warehouse", warehouse, "custom_branch"), [warehouse]
	if branch:
		return branch, (_get_branch_warehouses(branch) or None)
	return None, None


def _get_branch_warehouses(branch: str) -> list[str]:
	return frappe.get_all("Warehouse", filters={"custom_branch": branch, "disabled": 0}, pluck="name")


def _check_branch_permission(branch: str | None):
	"""Mirrors _resolve_context's Supplier permission check. A branch-restricted
	user might not see a given Branch in their own Link dropdown, but nothing
	stops them from passing it directly to a whitelisted call - so this closes
	that gap explicitly rather than relying on the UI alone."""
	if branch and not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)


@frappe.whitelist()
def get_vendor_performance_summary(
	supplier: str,
	company: str | None = None,
	lookback_days: int = 30,
	branch: str | None = None,
	warehouse: str | None = None,
):
	"""Cheap, aggregate-only KPIs for the top of the page. No per-item lists and
	no stock-ledger replay - those are fetched separately via the drill-through
	endpoints below only when the user asks for them."""
	supplier, company, supplier_meta, currency = _resolve_context(supplier, company)
	lookback_days, date_from, date_to = _get_date_window(lookback_days)
	branch, warehouse_list = _resolve_branch_warehouse_scope(branch, warehouse)
	_check_branch_permission(branch)

	item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
	stock_summary, _rows = _get_stock_position(
		item_codes=item_codes, company=company, item_limit=1, warehouse_list=warehouse_list
	)
	sales_summary = _get_sales_summary(
		item_codes=item_codes, company=company, date_from=date_from, date_to=date_to, branch=branch
	)
	cogs_summary = _get_cogs_summary(
		item_codes=item_codes,
		company=company,
		date_from=date_from,
		date_to=date_to,
		warehouse_list=warehouse_list,
	)
	purchase_summary = _get_purchase_summary(
		supplier=supplier, company=company, date_from=date_from, date_to=date_to, branch=branch
	)
	purchase_receipt_summary = _get_purchase_receipt_summary(
		supplier=supplier, company=company, date_from=date_from, date_to=date_to, branch=branch
	)
	outstanding_summary = _get_outstanding_summary(supplier=supplier, company=company, branch=branch)
	last_payment = _get_last_payment(supplier=supplier, company=company, branch=branch)

	sales_amount = flt(sales_summary.get("sales_amount"))
	cogs_amount = flt(cogs_summary.get("cogs_amount"))
	gross_margin_amount = sales_amount - cogs_amount
	gross_margin_pct = (gross_margin_amount / sales_amount * 100) if sales_amount else 0

	return {
		"supplier": supplier,
		"supplier_name": supplier_meta.supplier_name or supplier,
		"supplier_group": supplier_meta.supplier_group,
		"company": company,
		"currency": currency,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"lookback_days": lookback_days,
		"stock_definition_note": _(
			"Stock position shows current stock of supplier-linked SKUs valued at cost, not exact remaining units by supplier origin."
		),
		"origin_stock_definition_note": _(
			"Exact vendor-origin stock (at cost) is computed by replaying stock ledger FIFO layers and attributing incoming Purchase Receipt and Purchase Invoice stock to the source supplier. Loaded on demand since it can be heavy for high-volume vendors."
		),
		"item_sources_note": _(
			"Supplier-linked SKUs are resolved from Item Supplier, Item Default, and actual submitted purchase history."
		),
		"cogs_definition_note": _(
			"Cost of goods sold is the cost-basis value (from stock ledger valuation) of supplier-linked SKUs consumed by submitted sales in the selected window - not the retail sales revenue."
		),
		"summary": {
			"linked_item_count": len(item_codes),
			"stock_item_count": cint(stock_summary.get("item_count")),
			"stock_qty": flt(stock_summary.get("stock_qty")),
			"stock_value": flt(stock_summary.get("stock_value")),
			"sales_qty": flt(sales_summary.get("sales_qty")),
			"sales_amount": sales_amount,
			"sales_doc_count": cint(sales_summary.get("doc_count")),
			"cogs_qty": flt(cogs_summary.get("cogs_qty")),
			"cogs_amount": cogs_amount,
			"gross_margin_amount": gross_margin_amount,
			"gross_margin_pct": gross_margin_pct,
			"purchase_qty": flt(purchase_summary.get("purchase_qty")),
			"purchase_amount": flt(purchase_summary.get("purchase_amount")),
			"purchase_doc_count": cint(purchase_summary.get("doc_count")),
			"purchase_receipt_qty": flt(purchase_receipt_summary.get("purchase_qty")),
			"purchase_receipt_amount": flt(purchase_receipt_summary.get("purchase_amount")),
			"purchase_receipt_doc_count": cint(purchase_receipt_summary.get("doc_count")),
			"outstanding_amount": flt(outstanding_summary.get("outstanding_amount")),
			"outstanding_invoice_count": cint(outstanding_summary.get("invoice_count")),
		},
		"last_payment": last_payment,
	}


@frappe.whitelist()
def get_vendor_stock_detail(
	supplier: str,
	company: str | None = None,
	lookback_days: int = 30,
	item_limit: int = 15,
	branch: str | None = None,
	warehouse: str | None = None,
):
	"""Drill-through: current stock of supplier-linked SKUs, top N by value."""
	supplier, company, _meta, currency = _resolve_context(supplier, company)
	lookback_days, date_from, date_to = _get_date_window(lookback_days)
	item_limit = max(1, min(cint(item_limit or 15), 50))
	branch, warehouse_list = _resolve_branch_warehouse_scope(branch, warehouse)
	_check_branch_permission(branch)

	item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
	stock_summary, stock_rows = _get_stock_position(
		item_codes=item_codes, company=company, item_limit=item_limit, warehouse_list=warehouse_list
	)

	if stock_rows:
		row_item_codes = [row["item_code"] for row in stock_rows]
		sales_by_item = _get_sales_by_item(
			item_codes=row_item_codes,
			company=company,
			date_from=date_from,
			date_to=date_to,
			branch=branch,
		)
		cogs_by_item = _get_cogs_by_item(
			item_codes=row_item_codes,
			company=company,
			date_from=date_from,
			date_to=date_to,
			warehouse_list=warehouse_list,
		)
		last_purchase_by_item = _get_last_purchase_by_item(
			supplier=supplier,
			company=company,
			item_codes=row_item_codes,
			branch=branch,
		)
		for row in stock_rows:
			sales_item = sales_by_item.get(row["item_code"], {})
			cogs_item = cogs_by_item.get(row["item_code"], {})
			last_purchase = last_purchase_by_item.get(row["item_code"], {})
			row["sales_qty_in_window"] = flt(sales_item.get("sales_qty"))
			row["sales_amount_in_window"] = flt(sales_item.get("sales_amount"))
			row["cogs_qty_in_window"] = flt(cogs_item.get("cogs_qty"))
			row["cogs_amount_in_window"] = flt(cogs_item.get("cogs_amount"))
			row["gross_margin_in_window"] = row["sales_amount_in_window"] - row["cogs_amount_in_window"]
			row["last_purchase_date"] = last_purchase.get("posting_date")
			row["last_purchase_rate"] = flt(last_purchase.get("rate"))
			row["last_purchase_doc"] = last_purchase.get("purchase_invoice")
			row["last_purchase_doctype"] = last_purchase.get("doctype")

	return {
		"currency": currency,
		"summary": {
			"stock_item_count": cint(stock_summary.get("item_count")),
			"stock_qty": flt(stock_summary.get("stock_qty")),
			"stock_value": flt(stock_summary.get("stock_value")),
		},
		"stock_items": stock_rows,
	}


@frappe.whitelist()
def get_vendor_origin_stock_detail(
	supplier: str,
	company: str | None = None,
	item_limit: int = 15,
	branch: str | None = None,
	warehouse: str | None = None,
):
	"""Drill-through: exact vendor-origin remaining stock via FIFO replay.
	Guarded by _MAX_ORIGIN_STOCK_SLE_ROWS - a supplier with a very large linked-item
	ledger history returns too_large=True instead of blocking on the full replay.

	Note: when warehouse-scoped, this can't see stock that arrived via an
	inter-branch Material Transfer from a different warehouse - the transfer's
	incoming SLE row has no voucher_detail_no back to a Purchase Receipt/Invoice
	row, so transferred-in stock shows as unattributed even if it legitimately
	originated from this supplier. A known, accepted limitation of warehouse
	scoping here, not something this function tries to work around.
	"""
	supplier, company, _meta, currency = _resolve_context(supplier, company)
	item_limit = max(1, min(cint(item_limit or 15), 50))
	branch, warehouse_list = _resolve_branch_warehouse_scope(branch, warehouse)
	_check_branch_permission(branch)

	empty_summary = {
		"item_count": 0,
		"stock_qty": 0,
		"stock_value": 0,
		"unattributed_qty": 0,
		"unattributed_value": 0,
	}
	item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
	if not item_codes:
		return {
			"currency": currency,
			"too_large": False,
			"sle_row_count": 0,
			"summary": empty_summary,
			"origin_stock_items": [],
		}

	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	sle_row_count = cint(
		frappe.db.sql(
			f"""
            SELECT COUNT(*)
            FROM `tabStock Ledger Entry`
            WHERE is_cancelled = 0
              AND company = %(company)s
              AND item_code IN %(item_codes)s
              {warehouse_clause}
            """,
			{
				"company": company,
				"item_codes": tuple(item_codes),
				"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
			},
		)[0][0]
	)

	if sle_row_count > _MAX_ORIGIN_STOCK_SLE_ROWS:
		return {
			"currency": currency,
			"too_large": True,
			"sle_row_count": sle_row_count,
			"summary": empty_summary,
			"origin_stock_items": [],
		}

	origin_stock_summary, origin_stock_rows = _get_exact_vendor_origin_stock(
		supplier=supplier,
		company=company,
		item_codes=item_codes,
		item_limit=item_limit,
		warehouse_list=warehouse_list,
	)
	return {
		"currency": currency,
		"too_large": False,
		"sle_row_count": sle_row_count,
		"summary": {
			"item_count": cint(origin_stock_summary.get("item_count")),
			"stock_qty": flt(origin_stock_summary.get("stock_qty")),
			"stock_value": flt(origin_stock_summary.get("stock_value")),
			"unattributed_qty": flt(origin_stock_summary.get("unattributed_qty")),
			"unattributed_value": flt(origin_stock_summary.get("unattributed_value")),
		},
		"origin_stock_items": origin_stock_rows,
	}


@frappe.whitelist()
def get_vendor_recent_purchases(
	supplier: str,
	company: str | None = None,
	limit: int = 3,
	branch: str | None = None,
	warehouse: str | None = None,
):
	"""Drill-through: most recent submitted purchase invoices for this supplier."""
	supplier, company, _meta, _currency = _resolve_context(supplier, company)
	limit = max(1, min(cint(limit or 3), 20))
	branch, _warehouse_list = _resolve_branch_warehouse_scope(branch, warehouse)
	_check_branch_permission(branch)
	return {
		"recent_purchases": _get_recent_purchases(
			supplier=supplier, company=company, limit=limit, branch=branch
		)
	}


@frappe.whitelist()
def get_vendor_recent_sales(
	supplier: str,
	company: str | None = None,
	lookback_days: int = 30,
	limit: int = 3,
	branch: str | None = None,
	warehouse: str | None = None,
):
	"""Drill-through: most recent sales of supplier-linked SKUs within the window."""
	supplier, company, _meta, _currency = _resolve_context(supplier, company)
	_lookback_days, date_from, date_to = _get_date_window(lookback_days)
	limit = max(1, min(cint(limit or 3), 20))
	branch, warehouse_list = _resolve_branch_warehouse_scope(branch, warehouse)
	_check_branch_permission(branch)

	item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
	recent_sales = _get_recent_sales(
		item_codes=item_codes,
		company=company,
		date_from=date_from,
		date_to=date_to,
		limit=limit,
		branch=branch,
	)

	cogs_by_voucher = _get_cogs_by_voucher(
		voucher_names=[row["name"] for row in recent_sales],
		item_codes=item_codes,
		company=company,
		warehouse_list=warehouse_list,
	)
	for row in recent_sales:
		row["cogs_amount"] = flt(cogs_by_voucher.get(row["name"]))
		row["gross_margin"] = flt(row["vendor_amount"]) - row["cogs_amount"]

	return {"recent_sales": recent_sales}


@frappe.whitelist()
def get_vendor_abnormal_ratios(
	supplier: str,
	company: str | None = None,
	lookback_days: int = 90,
	branch: str | None = None,
	warehouse: str | None = None,
):
	"""Drill-through: flags supplier-linked SKUs that aren't selling through as fast
	as they should be, given how long ago they were actually received.

	This is a shrinkage/theft-adjacent signal, not a paperwork check: a vendor who
	physically under-delivers while the Purchase Receipt still records the agreed
	quantity (a deceived or colluding receiving count) won't show up as a PO-vs-PR
	document mismatch, since both documents agree with each other. It only surfaces
	later as fewer units than expected actually being available to sell.

	A flat purchased:sold ratio over a fixed window can't tell "80% sold in 2 days"
	(clearly fine - way ahead of any reasonable pace) apart from "80% sold in 30
	days" (not fine, if this item normally clears out faster) - both look like
	"ratio 1.25" with no time context. So instead, per item:

	1. Find how much was purchased in-window and the qty-weighted average date it
	   arrived (_get_purchase_events_by_item) - a single quantity-weighted date
	   rather than tracking each Purchase Receipt as a separate cohort, since
	   that's enough signal without per-invoice batch bookkeeping.
	2. Establish that item's own normal daily sell-through pace from a much longer,
	   stable trailing baseline (_VELOCITY_BASELINE_DAYS = 180 days, independent of
	   the shorter evaluation window) - this is also, incidentally, exactly the
	   kind of per-item velocity data a future automated-reorder/lead-time feature
	   would need, so this doubles as a first step toward that.
	3. expected_days_to_clear = purchased_qty / daily_rate is how long, at that
	   item's own normal pace, this delivery should take to sell out. Compare
	   actual elapsed days against that instead of against the fixed window:
	   still-early (elapsed < expected) is never flagged regardless of how little
	   has sold yet; once elapsed >= expected, a low sold percentage is what gets
	   flagged, with severity escalating the further below expected and the
	   further past the expected-clear point it is.

	Items too new/rarely sold to have a trustworthy personal baseline
	(< _VELOCITY_MIN_BASELINE_SOLD_QTY sold across the baseline window) can't use
	step 2-3, so they fall back to the previous approach instead: comparing their
	plain in-window purchased:sold ratio against the vendor's *other* fallback-method
	items via Tukey's IQR fence (or a fixed sanity multiple with too few items for
	that to be meaningful) - still no manually-tuned per-item threshold, which stays
	infeasible at 37,000+ SKUs sitewide either way.
	"""
	supplier, company, _meta, _currency = _resolve_context(supplier, company)
	lookback_days, date_from, date_to = _get_date_window(lookback_days)
	branch, warehouse_list = _resolve_branch_warehouse_scope(branch, warehouse)
	_check_branch_permission(branch)

	item_codes = _get_supplier_item_codes(supplier=supplier, company=company)
	if not item_codes:
		return {"items": [], "lookback_days": lookback_days, "flagged_count": 0, "critical_count": 0}

	purchase_events = _get_purchase_events_by_item(
		item_codes=item_codes,
		company=company,
		date_from=date_from,
		date_to=date_to,
		warehouse_list=warehouse_list,
	)
	velocity_baseline = _get_item_velocity_baseline(
		item_codes=item_codes, company=company, evaluation_date_from=date_from, warehouse_list=warehouse_list
	)
	# Only needed for items that fall back to plain in-window ratio comparison.
	window_sold_by_item = _get_cogs_by_item(
		item_codes=item_codes,
		company=company,
		date_from=date_from,
		date_to=date_to,
		warehouse_list=warehouse_list,
	)
	item_names = frappe.db.get_all(
		"Item", filters={"item_code": ["in", item_codes]}, fields=["item_code", "item_name"]
	)
	item_name_map = {row.item_code: row.item_name for row in item_names}

	today_date = getdate(today())
	velocity_rows = []
	fallback_rows = []

	for item_code in item_codes:
		event = purchase_events.get(item_code)
		if not event or not event["purchased_qty"]:
			continue  # nothing purchased in window - not evaluable for this check

		purchased_qty = event["purchased_qty"]
		purchase_date = event["weighted_purchase_date"]
		days_since_purchase = max(0, (today_date - purchase_date).days)
		base_row = {
			"item_code": item_code,
			"item_name": item_name_map.get(item_code) or item_code,
			"purchased_qty": purchased_qty,
			"purchase_date": str(purchase_date),
			"days_since_purchase": days_since_purchase,
		}

		baseline = velocity_baseline.get(item_code, {})
		daily_rate = flt(baseline.get("daily_rate"))
		baseline_sold_qty = flt(baseline.get("baseline_sold_qty"))

		if daily_rate > 0 and baseline_sold_qty >= _VELOCITY_MIN_BASELINE_SOLD_QTY:
			sold_qty = _get_sold_qty_since(
				item_code=item_code,
				company=company,
				since_date=purchase_date,
				date_to=date_to,
				warehouse_list=warehouse_list,
			)
			pct_sold = min(1.0, sold_qty / purchased_qty)
			expected_days_to_clear = purchased_qty / daily_rate
			progress_ratio = (days_since_purchase / expected_days_to_clear) if expected_days_to_clear else 0

			base_row.update(
				{
					"method": "velocity",
					"sold_qty": sold_qty,
					"pct_sold": pct_sold,
					"expected_days_to_clear": expected_days_to_clear,
				}
			)

			if progress_ratio < 1 or pct_sold >= 0.85:
				base_row["severity"] = "normal"
				base_row["flag_reason"] = None
			else:
				base_row["severity"] = "critical" if (pct_sold < 0.5 or progress_ratio >= 2) else "abnormal"
				base_row["flag_reason"] = _(
					"Only {0}% sold {1} days after receiving {2} units - at this item's normal pace it should have cleared in about {3} days."
				).format(
					round(pct_sold * 100),
					days_since_purchase,
					round(purchased_qty, 1),
					round(expected_days_to_clear),
				)

			velocity_rows.append(base_row)
		else:
			sold_qty = flt(window_sold_by_item.get(item_code, {}).get("cogs_qty"))
			base_row.update(
				{
					"method": "fallback",
					"sold_qty": sold_qty,
					"pct_sold": min(1.0, sold_qty / purchased_qty) if purchased_qty else 0.0,
					"ratio": (purchased_qty / sold_qty) if sold_qty else None,
				}
			)
			fallback_rows.append(base_row)

	fallback_ratios = [row["ratio"] for row in fallback_rows if row["ratio"] is not None]
	if len(fallback_ratios) >= 5:
		q1, q3 = _percentile(fallback_ratios, 25), _percentile(fallback_ratios, 75)
		iqr = q3 - q1
		upper_fence = q3 + 1.5 * iqr
	else:
		median_ratio = _percentile(fallback_ratios, 50) if fallback_ratios else 0.0
		# No stable distribution to lean on with this little data - fall back to a
		# fixed sanity multiple (purchasing >3x what's sold) plus a minimum absolute
		# quantity so a couple of stray units don't trip it on pure noise.
		upper_fence = max(3.0, median_ratio * 3)

	for row in fallback_rows:
		if row["sold_qty"] == 0 and row["purchased_qty"] > 0:
			row["severity"] = "critical"
			row["flag_reason"] = _(
				"No sales recorded in window despite {0} units purchased (too new/rarely sold for a personal pace baseline)."
			).format(row["purchased_qty"])
		elif row["ratio"] is not None and row["ratio"] > upper_fence and row["purchased_qty"] >= 5:
			row["severity"] = "abnormal"
			row["flag_reason"] = _(
				"Purchase:sale ratio {0} is well above this vendor's other items (too new/rarely sold for a personal pace baseline)."
			).format(round(row["ratio"], 2))
		else:
			row["severity"] = "normal"
			row["flag_reason"] = None

	rows = velocity_rows + fallback_rows
	severity_rank = {"critical": 0, "abnormal": 1, "normal": 2}
	rows.sort(
		key=lambda r: (severity_rank[r["severity"]], -(r["pct_sold"] if r["severity"] != "normal" else 0))
	)

	return {
		"items": rows,
		"lookback_days": lookback_days,
		"velocity_baseline_days": _VELOCITY_BASELINE_DAYS,
		"flagged_count": sum(1 for row in rows if row["severity"] != "normal"),
		"critical_count": sum(1 for row in rows if row["severity"] == "critical"),
	}


def _get_supplier_item_codes(supplier: str, company: str) -> list[str]:
	rows = frappe.db.sql(
		"""
        SELECT DISTINCT item_code
        FROM (
            SELECT isup.parent AS item_code
            FROM `tabItem Supplier` isup
            WHERE isup.supplier = %(supplier)s

            UNION

            SELECT idef.parent AS item_code
            FROM `tabItem Default` idef
            WHERE idef.default_supplier = %(supplier)s
              AND (idef.company = %(company)s OR idef.company IS NULL OR idef.company = '')

            UNION

            SELECT pii.item_code AS item_code
            FROM `tabPurchase Invoice Item` pii
            INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.supplier = %(supplier)s
              AND IFNULL(pii.item_code, '') != ''

            UNION

            SELECT pri.item_code AS item_code
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
            WHERE pr.docstatus = 1
              AND IFNULL(pr.is_return, 0) = 0
              AND pr.company = %(company)s
              AND pr.supplier = %(supplier)s
              AND IFNULL(pri.item_code, '') != ''
        ) item_pool
        WHERE IFNULL(item_code, '') != ''
        ORDER BY item_code
        """,
		{"supplier": supplier, "company": company},
		as_dict=True,
	)
	return [row.item_code for row in rows]


def _get_stock_position(
	item_codes: list[str], company: str, item_limit: int, warehouse_list: list[str] | None = None
):
	if not item_codes:
		return {"item_count": 0, "stock_qty": 0, "stock_value": 0}, []

	warehouse_clause = "AND b.warehouse IN %(warehouse_list)s" if warehouse_list else ""
	grouped_rows = frappe.db.sql(
		f"""
        SELECT
            b.item_code,
            i.item_name,
            SUM(b.actual_qty) AS stock_qty,
            SUM(b.stock_value) AS stock_value
        FROM `tabBin` b
        INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
        INNER JOIN `tabItem` i ON i.name = b.item_code
        WHERE b.item_code IN %(item_codes)s
          AND w.company = %(company)s
          {warehouse_clause}
        GROUP BY b.item_code, i.item_name
        HAVING ABS(SUM(b.actual_qty)) > 0.0001 OR ABS(SUM(b.stock_value)) > 0.0001
        ORDER BY stock_value DESC, stock_qty DESC, b.item_code ASC
        """,
		{
			"item_codes": tuple(item_codes),
			"company": company,
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)

	summary = {
		"item_count": len(grouped_rows),
		"stock_qty": sum(flt(row.stock_qty) for row in grouped_rows),
		"stock_value": sum(flt(row.stock_value) for row in grouped_rows),
	}

	rows = [
		{
			"item_code": row.item_code,
			"item_name": row.item_name,
			"stock_qty": flt(row.stock_qty),
			"stock_value": flt(row.stock_value),
		}
		for row in grouped_rows[:item_limit]
	]
	return summary, rows


def _get_exact_vendor_origin_stock(
	supplier: str,
	company: str,
	item_codes: list[str],
	item_limit: int,
	warehouse_list: list[str] | None = None,
):
	if not item_codes:
		return {
			"item_count": 0,
			"stock_qty": 0,
			"stock_value": 0,
			"unattributed_qty": 0,
			"unattributed_value": 0,
		}, []

	attribution_map = _get_purchase_supplier_map(company=company, item_codes=item_codes)
	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	sle_rows = frappe.db.sql(
		f"""
        SELECT
            name,
            item_code,
            voucher_type,
            voucher_no,
            voucher_detail_no,
            posting_date,
            posting_time,
            creation,
            actual_qty,
            stock_value_difference
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          {warehouse_clause}
        ORDER BY item_code ASC, posting_date ASC, posting_time ASC, creation ASC, name ASC
        """,
		{
			"company": company,
			"item_codes": tuple(item_codes),
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)

	item_names = {
		row.name: row.item_name
		for row in frappe.get_all("Item", filters={"name": ["in", item_codes]}, fields=["name", "item_name"])
	}

	layers_by_item: dict[str, list[dict]] = {item_code: [] for item_code in item_codes}
	unattributed_qty = 0.0
	unattributed_value = 0.0

	for row in sle_rows:
		item_code = row.item_code
		qty = flt(row.actual_qty)
		value = flt(row.stock_value_difference)
		if not qty:
			continue

		if qty > 0:
			supplier_name = attribution_map.get(row.voucher_detail_no)
			layer = {
				"qty": qty,
				"value": value,
				"supplier": supplier_name,
				"voucher_type": row.voucher_type,
				"voucher_no": row.voucher_no,
				"posting_date": str(row.posting_date),
			}
			layers_by_item.setdefault(item_code, []).append(layer)
			if not supplier_name:
				unattributed_qty += qty
				unattributed_value += value
			continue

		consume_qty = abs(qty)
		item_layers = layers_by_item.setdefault(item_code, [])
		idx = 0
		while consume_qty > 0.0000001 and idx < len(item_layers):
			layer = item_layers[idx]
			layer_qty = flt(layer["qty"])
			if layer_qty <= 0.0000001:
				idx += 1
				continue
			used_qty = min(layer_qty, consume_qty)
			unit_value = flt(layer["value"]) / layer_qty if layer_qty else 0
			layer["qty"] = layer_qty - used_qty
			layer["value"] = flt(layer["value"]) - (unit_value * used_qty)
			if not layer.get("supplier"):
				unattributed_qty -= used_qty
				unattributed_value -= unit_value * used_qty
			consume_qty -= used_qty
			if flt(layer["qty"]) <= 0.0000001:
				idx += 1

	per_item = []
	total_qty = 0.0
	total_value = 0.0
	for item_code, layers in layers_by_item.items():
		supplier_layers = [
			layer
			for layer in layers
			if layer.get("supplier") == supplier and flt(layer.get("qty")) > 0.0000001
		]
		if not supplier_layers:
			continue
		qty = sum(flt(layer["qty"]) for layer in supplier_layers)
		value = sum(flt(layer["value"]) for layer in supplier_layers)
		total_qty += qty
		total_value += value
		latest_layer = supplier_layers[-1]
		per_item.append(
			{
				"item_code": item_code,
				"item_name": item_names.get(item_code),
				"origin_stock_qty": qty,
				"origin_stock_value": value,
				"last_origin_doc": latest_layer.get("voucher_no"),
				"last_origin_doctype": latest_layer.get("voucher_type"),
				"last_origin_date": latest_layer.get("posting_date"),
			}
		)

	per_item.sort(
		key=lambda row: (-flt(row["origin_stock_value"]), -flt(row["origin_stock_qty"]), row["item_code"])
	)
	summary = {
		"item_count": len(per_item),
		"stock_qty": total_qty,
		"stock_value": total_value,
		"unattributed_qty": max(flt(unattributed_qty), 0),
		"unattributed_value": max(flt(unattributed_value), 0),
	}
	return summary, per_item[:item_limit]


def _get_purchase_supplier_map(company: str, item_codes: list[str]):
	if not item_codes:
		return {}

	rows = frappe.db.sql(
		"""
        SELECT pri.name AS row_name, pr.supplier
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
        WHERE pr.docstatus = 1
          AND IFNULL(pr.is_return, 0) = 0
          AND pr.company = %(company)s
          AND pri.item_code IN %(item_codes)s

        UNION ALL

        SELECT pii.name AS row_name, pi.supplier
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pii.item_code IN %(item_codes)s
        """,
		{"company": company, "item_codes": tuple(item_codes)},
		as_dict=True,
	)
	return {row.row_name: row.supplier for row in rows if row.row_name and row.supplier}


def _get_sales_summary(item_codes: list[str], company: str, date_from, date_to, branch: str | None = None):
	if not item_codes:
		return {"sales_qty": 0, "sales_amount": 0, "doc_count": 0}

	# POS Invoice only, deliberately not unioned with Sales Invoice Item: every
	# submitted Sales Invoice on this bench is itself a POS-consolidation output
	# (either a POS Closing Entry's consolidated_invoice or its
	# consolidated_credit_note for returns - confirmed on szl 2026-07-17, zero
	# standalone Sales Invoices exist), so unioning it back in here double-counts
	# the same underlying sale a second time. See the "Revenue double-counting
	# gotcha" note under the ai/ module in CLAUDE.md for the full investigation.
	pi_branch_clause = "AND pi.branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT
            SUM(pii.stock_qty) AS sales_qty,
            SUM(pii.base_net_amount) AS sales_amount,
            COUNT(DISTINCT pi.name) AS doc_count
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          AND pii.item_code IN %(item_codes)s
          {pi_branch_clause}
        """,
		{
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"item_codes": tuple(item_codes),
			"branch": branch,
		},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	return {
		"sales_qty": flt(row.get("sales_qty")),
		"sales_amount": flt(row.get("sales_amount")),
		"doc_count": cint(row.get("doc_count")),
	}


def _get_cogs_summary(
	item_codes: list[str], company: str, date_from, date_to, warehouse_list: list[str] | None = None
):
	"""Cost of goods sold: cost-basis value of stock consumed by submitted sales in
	the window, read straight from Stock Ledger Entry valuation (stock_value_difference)
	rather than sales revenue. Consumption rows have actual_qty < 0; a Sales/POS Invoice
	return instead adds stock back in with actual_qty > 0, so it's naturally excluded
	without needing to join back to the parent doc's is_return flag."""
	if not item_codes:
		return {"cogs_qty": 0, "cogs_amount": 0, "doc_count": 0}

	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	rows = frappe.db.sql(
		f"""
        SELECT
            COALESCE(SUM(-actual_qty), 0) AS cogs_qty,
            COALESCE(SUM(-stock_value_difference), 0) AS cogs_amount,
            COUNT(DISTINCT voucher_no) AS doc_count
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND (
              voucher_type IN ('Sales Invoice', 'POS Invoice')
              OR (voucher_type = 'Stock Entry' AND voucher_no IN %(historical_correction_entries)s)
          )
          AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {warehouse_clause}
        """,
		{
			"company": company,
			"item_codes": tuple(item_codes),
			"date_from": date_from,
			"date_to": date_to,
			"historical_correction_entries": _HISTORICAL_POS_STOCK_CORRECTION_ENTRIES,
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	return {
		"cogs_qty": flt(row.get("cogs_qty")),
		"cogs_amount": flt(row.get("cogs_amount")),
		"doc_count": cint(row.get("doc_count")),
	}


def _get_purchase_summary(supplier: str, company: str, date_from, date_to, branch: str | None = None):
	branch_clause = "AND branch = %(branch)s" if branch else ""
	branch_clause_pi = "AND pi.branch = %(branch)s" if branch else ""
	invoice_rows = frappe.db.sql(
		f"""
        SELECT COUNT(*) AS doc_count, COALESCE(SUM(base_grand_total), 0) AS purchase_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1
          AND IFNULL(is_return, 0) = 0
          AND company = %(company)s
          AND supplier = %(supplier)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
		{
			"supplier": supplier,
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch": branch,
		},
		as_dict=True,
	)
	qty_rows = frappe.db.sql(
		f"""
        SELECT COALESCE(SUM(pii.qty), 0) AS purchase_qty
        FROM `tabPurchase Invoice Item` pii
        INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.supplier = %(supplier)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause_pi}
        """,
		{
			"supplier": supplier,
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch": branch,
		},
		as_dict=True,
	)
	invoice_row = invoice_rows[0] if invoice_rows else {}
	qty_row = qty_rows[0] if qty_rows else {}
	return {
		"doc_count": cint(invoice_row.get("doc_count")),
		"purchase_qty": flt(qty_row.get("purchase_qty")),
		"purchase_amount": flt(invoice_row.get("purchase_amount")),
	}


def _get_purchase_receipt_summary(supplier: str, company: str, date_from, date_to, branch: str | None = None):
	"""Goods actually received (Purchase Receipt), separate from Purchase Invoice
	billing - the two can diverge (received-not-billed, billed-without-receipt for
	service/direct items), so this is tracked as its own card rather than folded
	into the Purchase Invoice numbers."""
	branch_clause = "AND branch = %(branch)s" if branch else ""
	branch_clause_pr = "AND pr.branch = %(branch)s" if branch else ""
	receipt_rows = frappe.db.sql(
		f"""
        SELECT COUNT(*) AS doc_count, COALESCE(SUM(base_grand_total), 0) AS purchase_amount
        FROM `tabPurchase Receipt`
        WHERE docstatus = 1
          AND IFNULL(is_return, 0) = 0
          AND company = %(company)s
          AND supplier = %(supplier)s
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause}
        """,
		{
			"supplier": supplier,
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch": branch,
		},
		as_dict=True,
	)
	qty_rows = frappe.db.sql(
		f"""
        SELECT COALESCE(SUM(pri.qty), 0) AS purchase_qty
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
        WHERE pr.docstatus = 1
          AND IFNULL(pr.is_return, 0) = 0
          AND pr.company = %(company)s
          AND pr.supplier = %(supplier)s
          AND pr.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {branch_clause_pr}
        """,
		{
			"supplier": supplier,
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch": branch,
		},
		as_dict=True,
	)
	receipt_row = receipt_rows[0] if receipt_rows else {}
	qty_row = qty_rows[0] if qty_rows else {}
	return {
		"doc_count": cint(receipt_row.get("doc_count")),
		"purchase_qty": flt(qty_row.get("purchase_qty")),
		"purchase_amount": flt(receipt_row.get("purchase_amount")),
	}


def _get_outstanding_summary(supplier: str, company: str, branch: str | None = None):
	branch_clause = "AND branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT COUNT(*) AS invoice_count, COALESCE(SUM(outstanding_amount), 0) AS outstanding_amount
        FROM `tabPurchase Invoice`
        WHERE docstatus = 1
          AND IFNULL(is_return, 0) = 0
          AND company = %(company)s
          AND supplier = %(supplier)s
          AND outstanding_amount > 0
          {branch_clause}
        """,
		{"supplier": supplier, "company": company, "branch": branch},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	return {
		"invoice_count": cint(row.get("invoice_count")),
		"outstanding_amount": flt(row.get("outstanding_amount")),
	}


def _get_last_payment(supplier: str, company: str, branch: str | None = None):
	branch_clause = "AND branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT name, posting_date, mode_of_payment, reference_no, paid_amount, paid_from_account_currency, base_paid_amount
        FROM `tabPayment Entry`
        WHERE docstatus = 1
          AND company = %(company)s
          AND party_type = 'Supplier'
          AND party = %(supplier)s
          AND payment_type = 'Pay'
          {branch_clause}
        ORDER BY posting_date DESC, modified DESC, name DESC
        LIMIT 1
        """,
		{"supplier": supplier, "company": company, "branch": branch},
		as_dict=True,
	)
	row = rows[0] if rows else None
	if not row:
		return None
	return {
		"payment_entry": row.name,
		"posting_date": str(row.posting_date),
		"mode_of_payment": row.mode_of_payment,
		"reference_no": row.reference_no,
		"amount": flt(row.base_paid_amount or row.paid_amount),
		"currency": row.paid_from_account_currency,
	}


def _get_recent_purchases(supplier: str, company: str, limit: int, branch: str | None = None):
	branch_clause = "AND pi.branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT pi.name, pi.posting_date, pi.bill_no, pi.base_grand_total, pi.outstanding_amount, pi.status
        FROM `tabPurchase Invoice` pi
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.supplier = %(supplier)s
          {branch_clause}
        ORDER BY pi.posting_date DESC, pi.modified DESC, pi.name DESC
        LIMIT %(limit)s
        """,
		{"supplier": supplier, "company": company, "limit": cint(limit), "branch": branch},
		as_dict=True,
	)
	return [
		{
			"doctype": "Purchase Invoice",
			"name": row.name,
			"posting_date": str(row.posting_date),
			"bill_no": row.bill_no,
			"amount": flt(row.base_grand_total),
			"outstanding_amount": flt(row.outstanding_amount),
			"status": row.status,
		}
		for row in rows
	]


def _get_recent_sales(
	item_codes: list[str], company: str, date_from, date_to, limit: int, branch: str | None = None
):
	if not item_codes:
		return []

	# POS Invoice only - see _get_sales_summary's comment above (and CLAUDE.md's
	# "Revenue double-counting gotcha" note): every submitted Sales Invoice here is
	# itself a POS-consolidation output, so including it would show the same sale
	# twice in this "recent sales" list, not just double-count a sum.
	pi_branch_clause = "AND pi.branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT 'POS Invoice' AS doctype, pi.name, pi.posting_date, pi.customer, pi.base_grand_total AS invoice_amount, SUM(pii.base_net_amount) AS vendor_amount
        FROM `tabPOS Invoice` pi
        INNER JOIN `tabPOS Invoice Item` pii ON pii.parent = pi.name
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          AND pii.item_code IN %(item_codes)s
          {pi_branch_clause}
        GROUP BY pi.name, pi.posting_date, pi.customer, pi.base_grand_total
        ORDER BY pi.posting_date DESC, pi.name DESC
        LIMIT %(limit)s
        """,
		{
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"item_codes": tuple(item_codes),
			"limit": cint(limit),
			"branch": branch,
		},
		as_dict=True,
	)
	return [
		{
			"doctype": row.doctype,
			"name": row.name,
			"posting_date": str(row.posting_date),
			"party": row.customer,
			"invoice_amount": flt(row.invoice_amount),
			"vendor_amount": flt(row.vendor_amount),
		}
		for row in rows
	]


def _get_sales_by_item(item_codes: list[str], company: str, date_from, date_to, branch: str | None = None):
	if not item_codes:
		return {}

	# POS Invoice only - see _get_sales_summary's comment above (and CLAUDE.md's
	# "Revenue double-counting gotcha" note): every submitted Sales Invoice here is
	# itself a POS-consolidation output, so unioning it back in double-counts.
	pi_branch_clause = "AND pi.branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT pii.item_code, SUM(pii.stock_qty) AS sales_qty, SUM(pii.base_net_amount) AS sales_amount
        FROM `tabPOS Invoice Item` pii
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
        WHERE pi.docstatus = 1
          AND IFNULL(pi.is_return, 0) = 0
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          AND pii.item_code IN %(item_codes)s
          {pi_branch_clause}
        GROUP BY pii.item_code
        """,
		{
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"item_codes": tuple(item_codes),
			"branch": branch,
		},
		as_dict=True,
	)
	return {
		row.item_code: {"sales_qty": flt(row.sales_qty), "sales_amount": flt(row.sales_amount)}
		for row in rows
	}


def _get_cogs_by_item(
	item_codes: list[str], company: str, date_from, date_to, warehouse_list: list[str] | None = None
):
	if not item_codes:
		return {}

	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	rows = frappe.db.sql(
		f"""
        SELECT
            item_code,
            SUM(-actual_qty) AS cogs_qty,
            SUM(-stock_value_difference) AS cogs_amount
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND (
              voucher_type IN ('Sales Invoice', 'POS Invoice')
              OR (voucher_type = 'Stock Entry' AND voucher_no IN %(historical_correction_entries)s)
          )
          AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {warehouse_clause}
        GROUP BY item_code
        """,
		{
			"company": company,
			"item_codes": tuple(item_codes),
			"date_from": date_from,
			"date_to": date_to,
			"historical_correction_entries": _HISTORICAL_POS_STOCK_CORRECTION_ENTRIES,
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)
	return {
		row.item_code: {"cogs_qty": flt(row.cogs_qty), "cogs_amount": flt(row.cogs_amount)} for row in rows
	}


def _get_purchase_events_by_item(
	item_codes: list[str], company: str, date_from, date_to, warehouse_list: list[str] | None = None
):
	"""Physical goods-in per item within the evaluation window, read from Stock
	Ledger Entry (Purchase Receipt only, not Purchase Invoice) - plus a single
	qty-weighted average arrival date per item, used as "when this delivery
	landed" for the abnormal-ratio velocity check without needing to track each
	Purchase Receipt as a separate cohort."""
	if not item_codes:
		return {}

	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	rows = frappe.db.sql(
		f"""
        SELECT item_code, posting_date, SUM(actual_qty) AS qty
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND voucher_type = 'Purchase Receipt'
          AND actual_qty > 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {warehouse_clause}
        GROUP BY item_code, posting_date
        """,
		{
			"company": company,
			"item_codes": tuple(item_codes),
			"date_from": date_from,
			"date_to": date_to,
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)

	events_by_item = defaultdict(list)
	for row in rows:
		events_by_item[row.item_code].append((getdate(row.posting_date), flt(row.qty)))

	result = {}
	for item_code, events in events_by_item.items():
		total_qty = sum(qty for _date, qty in events)
		if not total_qty:
			continue
		weighted_ordinal = sum(d.toordinal() * qty for d, qty in events) / total_qty
		result[item_code] = {
			"purchased_qty": total_qty,
			"weighted_purchase_date": date.fromordinal(round(weighted_ordinal)),
		}
	return result


def _get_item_velocity_baseline(
	item_codes: list[str],
	company: str,
	evaluation_date_from,
	baseline_days: int = _VELOCITY_BASELINE_DAYS,
	warehouse_list: list[str] | None = None,
):
	"""Each item's own normal daily sell-through pace, from a long trailing window
	that ends *before* the evaluation window starts (not "the last N days ending
	today"). This matters: the evaluation window's own sales would otherwise leak
	into the baseline they're being judged against, so a genuinely underselling
	delivery would drag its own baseline down with it and end up looking normal
	against itself - especially visible for items with little sales history
	predating the window being evaluated. Scoped by the same warehouse_list as
	the rest of the abnormal-ratios check when one is active, for an apples-to-
	apples comparison (branch-local actual pace vs. branch-local normal pace,
	not branch-local actual vs. sitewide normal)."""
	if not item_codes:
		return {}

	baseline_date_to = getdate(add_days(evaluation_date_from, -1))
	baseline_date_from = getdate(add_days(baseline_date_to, -(baseline_days - 1)))

	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	rows = frappe.db.sql(
		f"""
        SELECT item_code, SUM(-actual_qty) AS sold_qty
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND (
              voucher_type IN ('Sales Invoice', 'POS Invoice')
              OR (voucher_type = 'Stock Entry' AND voucher_no IN %(historical_correction_entries)s)
          )
          AND actual_qty < 0
          AND posting_date BETWEEN %(date_from)s AND %(date_to)s
          {warehouse_clause}
        GROUP BY item_code
        """,
		{
			"company": company,
			"item_codes": tuple(item_codes),
			"date_from": baseline_date_from,
			"date_to": baseline_date_to,
			"historical_correction_entries": _HISTORICAL_POS_STOCK_CORRECTION_ENTRIES,
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)
	result = {}
	for row in rows:
		sold_qty = flt(row.sold_qty)
		result[row.item_code] = {
			"baseline_sold_qty": sold_qty,
			"daily_rate": sold_qty / baseline_days if baseline_days else 0,
		}
	return result


def _get_sold_qty_since(
	item_code: str, company: str, since_date, date_to, warehouse_list: list[str] | None = None
):
	"""Single-item lookup: qty sold from a specific date onward. Only ever called
	for the handful of items a vendor-performance drill-through evaluates at once
	(not sitewide), so a per-item query here is cheap."""
	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	rows = frappe.db.sql(
		f"""
        SELECT COALESCE(SUM(-actual_qty), 0) AS sold_qty
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code = %(item_code)s
          AND (
              voucher_type IN ('Sales Invoice', 'POS Invoice')
              OR (voucher_type = 'Stock Entry' AND voucher_no IN %(historical_correction_entries)s)
          )
          AND actual_qty < 0
          AND posting_date BETWEEN %(since_date)s AND %(date_to)s
          {warehouse_clause}
        """,
		{
			"company": company,
			"item_code": item_code,
			"since_date": since_date,
			"date_to": date_to,
			"historical_correction_entries": _HISTORICAL_POS_STOCK_CORRECTION_ENTRIES,
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)
	return flt(rows[0].sold_qty) if rows else 0.0


def _percentile(values: list[float], pct: float) -> float:
	"""Linear-interpolation percentile (matches numpy's default), no external
	dependency needed for the small per-vendor item lists this runs on."""
	if not values:
		return 0.0
	ordered = sorted(values)
	if len(ordered) == 1:
		return ordered[0]
	k = (len(ordered) - 1) * (pct / 100)
	lower_idx = int(k)
	upper_idx = min(lower_idx + 1, len(ordered) - 1)
	fraction = k - lower_idx
	return ordered[lower_idx] + (ordered[upper_idx] - ordered[lower_idx]) * fraction


def _get_cogs_by_voucher(
	voucher_names: list[str], item_codes: list[str], company: str, warehouse_list: list[str] | None = None
):
	if not voucher_names or not item_codes:
		return {}

	warehouse_clause = "AND warehouse IN %(warehouse_list)s" if warehouse_list else ""
	rows = frappe.db.sql(
		f"""
        SELECT voucher_no, SUM(-stock_value_difference) AS cogs_amount
        FROM `tabStock Ledger Entry`
        WHERE is_cancelled = 0
          AND company = %(company)s
          AND item_code IN %(item_codes)s
          AND voucher_type IN ('Sales Invoice', 'POS Invoice')
          AND voucher_no IN %(voucher_names)s
          AND actual_qty < 0
          {warehouse_clause}
        GROUP BY voucher_no
        """,
		{
			"company": company,
			"item_codes": tuple(item_codes),
			"voucher_names": tuple(voucher_names),
			"warehouse_list": tuple(warehouse_list) if warehouse_list else (),
		},
		as_dict=True,
	)
	return {row.voucher_no: flt(row.cogs_amount) for row in rows}


def _get_last_purchase_by_item(supplier: str, company: str, item_codes: list[str], branch: str | None = None):
	if not item_codes:
		return {}

	pi_branch_clause = "AND pi.branch = %(branch)s" if branch else ""
	pr_branch_clause = "AND pr.branch = %(branch)s" if branch else ""
	rows = frappe.db.sql(
		f"""
        SELECT *
        FROM (
            SELECT pii.item_code, 'Purchase Invoice' AS doctype, pi.name AS document_name, pi.posting_date, pii.rate, pi.modified
            FROM `tabPurchase Invoice Item` pii
            INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus = 1
              AND IFNULL(pi.is_return, 0) = 0
              AND pi.company = %(company)s
              AND pi.supplier = %(supplier)s
              AND pii.item_code IN %(item_codes)s
              {pi_branch_clause}

            UNION ALL

            SELECT pri.item_code, 'Purchase Receipt' AS doctype, pr.name AS document_name, pr.posting_date, pri.rate, pr.modified
            FROM `tabPurchase Receipt Item` pri
            INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
            WHERE pr.docstatus = 1
              AND IFNULL(pr.is_return, 0) = 0
              AND pr.company = %(company)s
              AND pr.supplier = %(supplier)s
              AND pri.item_code IN %(item_codes)s
              {pr_branch_clause}
        ) purchase_rows
        ORDER BY posting_date DESC, modified DESC, document_name DESC
        """,
		{"supplier": supplier, "company": company, "item_codes": tuple(item_codes), "branch": branch},
		as_dict=True,
	)
	latest = {}
	for row in rows:
		if row.item_code not in latest:
			latest[row.item_code] = {
				"purchase_invoice": row.document_name,
				"doctype": row.doctype,
				"posting_date": str(row.posting_date),
				"rate": flt(row.rate),
			}
	return latest
