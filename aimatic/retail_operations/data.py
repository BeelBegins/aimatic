"""Bounded branch-scoped evidence. Callers must authorise the branch first."""

from __future__ import annotations

from collections import defaultdict
from statistics import median, pstdev

import frappe
from frappe.utils import add_days, flt, getdate, now_datetime, today

from aimatic.retail_operations.calculations import (
	VERSION,
	number,
	recommend,
	reference_candidates,
	vendor_packs,
)


def sql(query, args):
	return frappe.db.sql(query, args, as_dict=True)


def inventory(branch, settings, cutoff=None):
	cutoff = getdate(cutoff or today())
	end = add_days(cutoff, -1)
	start = add_days(cutoff, -int(settings.history_days or 90))
	p = {
		"branch": branch.name,
		"company": branch.company,
		"warehouse": branch.finished_goods_warehouse,
		"start": start,
		"end": end,
		"recent": add_days(cutoff, -7),
		"month": add_days(cutoff, -28),
		"today": cutoff,
	}
	stock = sql(
		"""SELECT b.item_code,i.item_name,i.item_group,i.brand,i.stock_uom,i.disabled,i.has_batch_no,i.has_expiry_date,
          b.actual_qty,b.reserved_qty,b.reserved_qty_for_production,b.reserved_qty_for_sub_contract,
          COALESCE(u.must_be_whole_number,0) whole_units
        FROM tabBin b JOIN tabItem i ON i.name=b.item_code LEFT JOIN tabUOM u ON u.name=i.stock_uom
        WHERE b.warehouse=%(warehouse)s AND i.is_stock_item=1""",
		p,
	)
	positions = {r.item_code: dict(r) for r in stock}
	daily = sql(
		"""SELECT pii.item_code,pi.posting_date,SUM(IF(pi.is_return=0,pii.stock_qty,0)) sold,
          SUM(IF(pi.is_return=1,ABS(pii.stock_qty),0)) returned
        FROM `tabPOS Invoice Item` pii JOIN `tabPOS Invoice` pi ON pi.name=pii.parent
        WHERE pi.docstatus=1 AND pi.company=%(company)s AND pi.branch=%(branch)s
          AND pii.warehouse=%(warehouse)s AND pi.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY pii.item_code,pi.posting_date""",
		p,
	)
	first = sql(
		"""SELECT MIN(posting_date) first_date FROM `tabPOS Invoice`
        WHERE docstatus=1 AND company=%(company)s AND branch=%(branch)s""",
		p,
	)[0].first_date
	denominator = max(1, min(28, (end - getdate(first)).days + 1)) if first else 28
	by_item = defaultdict(list)
	for d in daily:
		by_item[d.item_code].append(d)
		if d.item_code not in positions:
			item = frappe.db.get_value(
				"Item",
				d.item_code,
				["item_name", "item_group", "brand", "stock_uom", "disabled"],
				as_dict=True,
			)
			positions[d.item_code] = dict(
				item or {}, item_code=d.item_code, actual_qty=0, flags=["No Bin: stock evidence incomplete"]
			)
	pending = sql(
		"""SELECT pii.item_code,SUM(pii.stock_qty) qty,MIN(pi.posting_date) oldest
        FROM `tabPOS Invoice Item` pii JOIN `tabPOS Invoice` pi ON pi.name=pii.parent
        WHERE pi.docstatus=1 AND pi.company=%(company)s AND pi.branch=%(branch)s
          AND pii.warehouse=%(warehouse)s AND IFNULL(pi.consolidated_invoice,'')=''
        GROUP BY pii.item_code""",
		p,
	)
	for r in pending:
		if r.item_code in positions:
			positions[r.item_code]["pending_pos_qty"] = flt(r.qty)
			if getdate(r.oldest) < add_days(cutoff, -1):
				positions[r.item_code].setdefault("flags", []).append(
					"Historical unconsolidated POS: verify stock impact"
				)
	receipts = sql(
		"""SELECT pri.item_code,pr.supplier,pr.posting_date date,pr.name receipt,
          pri.purchase_order,pri.stock_qty qty,pri.base_net_rate/NULLIF(pri.conversion_factor,0) rate,
          pr.is_return
        FROM `tabPurchase Receipt Item` pri JOIN `tabPurchase Receipt` pr ON pr.name=pri.parent
        WHERE pr.docstatus=1 AND pr.company=%(company)s AND pri.warehouse=%(warehouse)s
          AND pr.posting_date BETWEEN %(start)s AND %(today)s ORDER BY pr.posting_date,pr.name""",
		p,
	)
	history = defaultdict(list)
	vendor_counts = defaultdict(lambda: defaultdict(set))
	last = {}
	for r in receipts:
		if not r.is_return and flt(r.qty) > 0:
			history[r.item_code].append(r)
			vendor_counts[r.item_code][r.supplier].add(r.receipt)
			last[(r.item_code, r.supplier)] = r
	for item, vendors in vendor_counts.items():
		if item not in positions:
			continue
		ranked = sorted(vendors, key=lambda v: (len(vendors[v]), str(last[(item, v)].date)), reverse=True)
		chosen = ranked[0]
		row = positions[item]
		latest = last[(item, chosen)]
		row.update(
			supplier=chosen,
			last_rate=flt(latest.rate),
			last_receipt=latest.receipt,
			last_receipt_date=str(latest.date),
			last_received_qty=flt(latest.qty),
			previous_purchase_order=latest.purchase_order,
		)
		if len(ranked) > 1 and len(vendors[ranked[0]]) == len(vendors[ranked[1]]):
			row.setdefault("flags", []).append("Several equally supported suppliers: confirm route")
	transfers = sql(
		"""SELECT sed.item_code,sed.s_warehouse,MAX(se.posting_date) latest,
          COUNT(DISTINCT se.name) receipts,SUM(sed.transfer_qty) qty
        FROM `tabStock Entry Detail` sed JOIN `tabStock Entry` se ON se.name=sed.parent
        WHERE se.docstatus=1 AND se.stock_entry_type='Material Transfer'
          AND sed.t_warehouse=%(warehouse)s AND sed.s_warehouse!=sed.t_warehouse
          AND se.posting_date BETWEEN %(start)s AND %(today)s
        GROUP BY sed.item_code,sed.s_warehouse ORDER BY receipts DESC,latest DESC""",
		p,
	)
	for t in transfers:
		if (
			t.item_code in positions
			and not positions[t.item_code].get("supplier")
			and not positions[t.item_code].get("source_warehouse")
		):
			positions[t.item_code]["source_warehouse"] = t.s_warehouse
	rules = frappe.get_all(
		"Retail Replenishment Rule",
		filters={"branch": branch.name, "enabled": 1},
		fields=["name", "item_code", "supplier", "source_warehouse", "reviewed", "automatic_request"],
	)
	for rule in rules:
		if rule.item_code in positions:
			positions[rule.item_code].update(
				rule=rule.name,
				supplier=rule.supplier,
				source_warehouse=rule.source_warehouse,
				route_reviewed=rule.reviewed,
			)
			if rule.reviewed:
				positions[rule.item_code]["flags"] = [
					f
					for f in positions[rule.item_code].get("flags", [])
					if not f.startswith("Several equally")
				]
	supply = sql(
		"""SELECT poi.name,poi.item_code,po.name purchase_order,po.supplier,poi.schedule_date,c.expected_on,
          GREATEST(poi.qty-poi.received_qty,0)*poi.conversion_factor qty
        FROM `tabPurchase Order Item` poi JOIN `tabPurchase Order` po ON po.name=poi.parent
        LEFT JOIN `tabRetail Commitment Item` ci ON ci.purchase_order_item=poi.name
        LEFT JOIN `tabRetail Purchase Commitment` c ON c.name=ci.parent AND c.status IN ('Placed','Delayed')
        WHERE po.docstatus=1 AND po.company=%(company)s AND poi.warehouse=%(warehouse)s
          AND po.status NOT IN ('Closed','Completed','Cancelled') AND poi.qty>poi.received_qty""",
		p,
	)
	for r in supply:
		if r.item_code in positions:
			bucket = (
				"open_supply_qty"
				if r.expected_on and getdate(r.expected_on) >= cutoff
				else "unknown_supply_qty"
			)
			positions[r.item_code][bucket] = positions[r.item_code].get(bucket, 0) + flt(r.qty)
			positions[r.item_code].setdefault("open_orders", []).append(dict(r))
	# Commitments matched to a native PO use native outstanding supply exclusively.
	commitments = sql(
		"""SELECT c.name,c.expected_on,ci.item_code,ci.qty,ci.purchase_order_item,ci.purchase_receipt_item
        FROM `tabRetail Commitment Item` ci JOIN `tabRetail Purchase Commitment` c ON c.name=ci.parent
        WHERE c.branch=%(branch)s AND c.company=%(company)s AND c.warehouse=%(warehouse)s
          AND c.status IN ('Placed','Delayed')""",
		p,
	)
	from aimatic.retail_operations.commitments import receipt_qty

	allocations = defaultdict(float)
	allocated = sql(
		"""SELECT a.parent,a.item_code,a.purchase_receipt_item FROM `tabRetail Commitment Receipt` a
        JOIN `tabRetail Purchase Commitment` c ON c.name=a.parent
        WHERE c.branch=%(branch)s AND c.status IN ('Placed','Delayed')""",
		p,
	)
	for allocation in allocated:
		allocations[(allocation.parent, allocation.item_code)] += receipt_qty(
			allocation.purchase_receipt_item
		)
	for c in commitments:
		if c.item_code not in positions:
			continue
		if c.purchase_order_item:
			po = frappe.db.get_value("Purchase Order Item", c.purchase_order_item, "parent")
			if po and frappe.db.get_value("Purchase Order", po, "docstatus") == 1:
				continue
			positions[c.item_code].setdefault("flags", []).append(
				"Matched order cancelled or missing: review outstanding commitment"
			)
		remaining = max(flt(c.qty) - allocations[(c.name, c.item_code)], 0)
		if c.purchase_receipt_item:
			received = frappe.db.get_value(
				"Purchase Receipt Item", c.purchase_receipt_item, ["parent", "stock_qty"], as_dict=True
			)
			if received and frappe.db.get_value("Purchase Receipt", received.parent, "docstatus") == 1:
				returns = sql(
					"""SELECT COALESCE(SUM(pri.stock_qty),0) qty FROM `tabPurchase Receipt Item` pri
                    JOIN `tabPurchase Receipt` pr ON pr.name=pri.parent
                    WHERE pr.docstatus=1 AND pr.is_return=1 AND pri.purchase_receipt_item=%s""",
					(c.purchase_receipt_item,),
				)[0].qty
				remaining = max(remaining - max(flt(received.stock_qty) + flt(returns), 0), 0)
		if remaining:
			bucket = (
				"open_supply_qty"
				if c.expected_on and getdate(c.expected_on) >= cutoff
				else "unknown_supply_qty"
			)
			positions[c.item_code][bucket] = positions[c.item_code].get(bucket, 0) + remaining
	requests = sql(
		"""SELECT mri.item_code,SUM(GREATEST(mri.stock_qty-mri.ordered_qty,0)) qty
        FROM `tabMaterial Request Item` mri JOIN `tabMaterial Request` mr ON mr.name=mri.parent
        WHERE mr.docstatus=1 AND mr.company=%(company)s AND mri.warehouse=%(warehouse)s
          AND mr.status NOT IN ('Stopped','Cancelled','Ordered','Received','Issued','Transferred')
        GROUP BY mri.item_code""",
		p,
	)
	for r in requests:
		if r.item_code in positions:
			positions[r.item_code]["requested_qty"] = flt(r.qty)
	outgoing = sql(
		"""SELECT mri.item_code,SUM(GREATEST(mri.stock_qty-mri.ordered_qty,0)) qty
        FROM `tabMaterial Request Item` mri JOIN `tabMaterial Request` mr ON mr.name=mri.parent
        WHERE mr.docstatus=1 AND mr.company=%(company)s AND mri.from_warehouse=%(warehouse)s
          AND mr.material_request_type='Material Transfer' AND mr.status NOT IN ('Stopped','Cancelled','Transferred')
        GROUP BY mri.item_code""",
		p,
	)
	for transfer in outgoing:
		if transfer.item_code in positions:
			positions[transfer.item_code]["outgoing_transfer_qty"] = flt(transfer.qty)
	refs = sql(
		"""SELECT r.name,r.status,r.primary_item,i.item_code FROM `tabRetail Product Reference` r
        JOIN `tabRetail Reference Item` i ON i.parent=r.name
        WHERE r.branch=%(branch)s""",
		p,
	)
	groups = defaultdict(list)
	rejected = defaultdict(set)
	for r in refs:
		if r.status == "Rejected":
			rejected[r.name].add(r.item_code)
			continue
		groups[r.name].append(r.item_code)
		if r.item_code in positions:
			positions[r.item_code].setdefault("flags", []).append(
				"Linked/suspected product references: verify combined availability before quantity automation"
			)
			positions[r.item_code]["reference_group"] = r.name
	candidates = [
		c for c in reference_candidates(list(positions.values())) if set(c["items"]) not in rejected.values()
	]
	for candidate in candidates:
		for item in candidate["items"]:
			positions[item].setdefault("flags", []).append(
				"Possible split item reference with matching product identity"
			)
	rows = []
	for item, row in positions.items():
		if row.get("has_batch_no") or row.get("has_expiry_date"):
			row.setdefault("flags", []).append("Batch/expiry stock availability requires review")
		observations = by_item[item]
		monthly = [d for d in observations if getdate(d.posting_date) >= p["month"]]
		row["sales_qty"] = sum(flt(d.sold) for d in monthly)
		row["recent_sales_qty"] = sum(
			flt(d.sold) for d in observations if getdate(d.posting_date) >= p["recent"]
		)
		row["returns_qty"] = sum(flt(d.returned) for d in monthly)
		values = [flt(d.sold) for d in monthly] + [0] * max(denominator - len(monthly), 0)
		row["daily_sd"] = pstdev(values) if values else 0
		row["whole_units"] = bool(row.get("whole_units")) or row.get("stock_uom") in (
			"Pcs",
			"Nos",
			"Unit",
			"Box",
			"Pack",
		)
		category = (row.get("item_group") or "").lower()
		cover = (
			2
			if any(w in category for w in ("bread", "fresh", "eggs", "dairy", "milk"))
			else int(settings.fallback_cover_days or 7)
		)
		row.update(branch=branch.name, warehouse=branch.finished_goods_warehouse, history_days=denominator)
		from aimatic.retail_operations.calculations import replay_coverage

		replay = replay_coverage(
			history[item],
			[{"date": d.posting_date, "qty": d.sold} for d in observations],
			cap_days=cover if cover <= 2 else 28,
		)
		row["coverage_replay"] = replay
		if replay.get("selected_days"):
			row["calibrated_cover_days"] = replay["selected_days"]
		result = recommend(row, history[item], denominator, cover)
		# A future PO is real supply but may arrive after current stock runs out.
		for order in result.get("open_orders", []):
			if (
				order.get("expected_on")
				and result["days_cover"] is not None
				and (getdate(order["expected_on"]) - cutoff).days > result["days_cover"]
			):
				result["flags"].append(
					"Open order arrives after estimated stockout: expedite existing supply"
				)
				result["auto_eligible"] = False
		rows.append(result)
	rows.sort(
		key=lambda r: (
			r["status"] not in ("Negative stock", "Stockout risk", "Low cover"),
			r.get("days_cover") if r.get("days_cover") is not None else 1e9,
		)
	)
	from aimatic.retail_operations.transfers import allocate

	allocate(rows, branch.company)
	summary = {
		status: sum(r["status"] == status for r in rows)
		for status in ("Negative stock", "Stockout risk", "Low cover", "Overstock", "Slow moving", "Healthy")
	}
	movement = sql(
		"""SELECT item_code,voucher_type,COUNT(*) entries,SUM(actual_qty) recorded_delta,
          SUM(stock_value_difference) value_delta,MAX(posting_date) latest
        FROM `tabStock Ledger Entry` WHERE is_cancelled=0 AND warehouse=%(warehouse)s
          AND posting_date=%(end)s GROUP BY item_code,voucher_type
        ORDER BY ABS(SUM(stock_value_difference)) DESC LIMIT 100""",
		p,
	)
	return dict(
		largest_movements=movement,
		movement_note="Largest recorded movements by absolute value on the last complete day; not proof of an abnormal physical movement. Reconciliation deltas need the before/after balance.",
		rows=rows,
		summary=summary,
		vendors=vendor_packs(rows),
		reference_candidates=candidates,
		reference_groups=dict(groups),
		reference_assessments=[
			{
				"reference": key,
				"items": members,
				"combined_ledger_diagnostic": sum(
					number(positions.get(i, {}).get("actual_qty")) for i in members
				),
				"combined_sales_28": sum(number(positions.get(i, {}).get("sales_qty")) for i in members),
				"note": "Identity grouping does not establish physical availability; no automatic ledger netting.",
			}
			for key, members in groups.items()
		],
		history_from=str(start),
		history_to=str(end),
		history_days=denominator,
		units_note="Quantities are stock units; no inferred carton conversions.",
		warnings=[
			"Coverage uses receipt patterns, not supplier lead times.",
			"Unrecorded placed orders must be entered as purchasing commitments.",
		],
	)


def sales(branch, settings, business_date):
	p = dict(
		branch=branch.name,
		company=branch.company,
		date=getdate(business_date),
		start=add_days(business_date, -28),
	)
	counters = sql(
		"""SELECT pos_profile,COUNT(*) receipts,SUM(IF(is_return=0,grand_total,0)) gross_sales,
        SUM(IF(is_return=1,ABS(grand_total),0)) refunds,SUM(is_return=1) refund_count,
        SUM(IFNULL(consolidated_invoice,'')='') pending_closing
        FROM `tabPOS Invoice` WHERE docstatus=1 AND branch=%(branch)s AND company=%(company)s
        AND posting_date=%(date)s GROUP BY pos_profile""",
		p,
	)
	history = sql(
		"""SELECT ABS(grand_total) amount FROM `tabPOS Invoice`
        WHERE docstatus=1 AND branch=%(branch)s AND company=%(company)s AND is_return=1
          AND posting_date BETWEEN %(start)s AND %(date)s ORDER BY ABS(grand_total) LIMIT 10000""",
		p,
	)
	values = [flt(r.amount) for r in history]
	threshold = max(
		flt(settings.refund_alert_amount), values[int((len(values) - 1) * 0.95)] if len(values) >= 20 else 0
	)
	large = sql(
		"""SELECT name,pos_profile,ABS(grand_total) amount,return_against FROM `tabPOS Invoice`
        WHERE docstatus=1 AND branch=%(branch)s AND company=%(company)s AND is_return=1
          AND posting_date=%(date)s AND ABS(grand_total)>=%(threshold)s ORDER BY ABS(grand_total) DESC LIMIT 100""",
		dict(p, threshold=threshold),
	)
	count = sql(
		"""SELECT COUNT(*) qty FROM `tabPOS Invoice Item` pii JOIN `tabPOS Invoice` pi ON pi.name=pii.parent
        WHERE pi.docstatus=1 AND pi.company=%(company)s AND pi.branch=%(branch)s AND pi.posting_date=%(date)s""",
		p,
	)[0].qty
	margins = {"status": "Unavailable", "reason": "Daily margin detail exceeds configured bound"}
	if count <= int(settings.max_margin_rows):
		from aimatic.aimatic.report.pos_invoice_gross_margin.pos_invoice_gross_margin import (
			_set_margin_values,
			get_rows,
		)

		evidence = get_rows(
			frappe._dict(
				company=branch.company,
				branch=branch.name,
				from_date=p["date"],
				to_date=p["date"],
				include_pending=1,
				include_returns=1,
			)
		)
		items = {}
		pending = 0
		for raw in evidence:
			r = _set_margin_values(raw, 2)
			if not r.get("has_ledger_cogs"):
				pending += 1
				continue
			# Rankings describe sold lines; returns stay in the financial totals separately.
			if r.is_return:
				continue
			item = items.setdefault(
				r.item_code, dict(item_code=r.item_code, item_name=r.item_name, sales=0, cogs=0, qty=0)
			)
			item["sales"] += flt(r.sales)
			item["cogs"] += flt(r.cogs)
			item["qty"] += flt(r.stock_qty)
		for item in items.values():
			item["profit"] = round(item["sales"] - item["cogs"], 2)
			item["margin_percent"] = (
				round(item["profit"] / abs(item["sales"]) * 100, 2) if item["sales"] else None
			)
		ranked = sorted(
			(r for r in items.values() if r["margin_percent"] is not None), key=lambda r: r["margin_percent"]
		)
		margins = dict(
			status="Provisional" if pending else "Ledger cost",
			pending_cost_lines=pending,
			lowest=ranked[:10],
			highest=list(reversed(ranked[-10:])),
			negative=[r for r in ranked if r["profit"] < 0][:100],
			note="Recorded COGS, excluding tax; reference/valuation anomalies still require review. Rankings show volume and exclude return lines.",
		)
	voids = (
		sql(
			"""SELECT pos_profile,kind event_type,COUNT(*) events,SUM(total_amount) amount,
        SUM(authorized_by='cashier_privilege') without_supervisor FROM `tabPOS Void Log`
        WHERE branch=%(branch)s AND company=%(company)s AND DATE(occurred_at)=%(date)s
        GROUP BY pos_profile,kind""",
			p,
		)
		if frappe.db.table_exists("POS Void Log")
		else []
	)
	cancelled = sql(
		"""SELECT pos_profile,COUNT(*) invoices FROM `tabPOS Invoice`
        WHERE branch=%(branch)s AND company=%(company)s AND docstatus=2 AND posting_date=%(date)s GROUP BY pos_profile""",
		p,
	)
	for c in counters:
		c["refund_activity_percent"] = (
			round(flt(c.refunds) / flt(c.gross_sales) * 100, 2) if c.gross_sales else None
		)
		c["comparison_eligible"] = c.receipts >= int(settings.counter_min_receipts)
	ranked_counters = sorted(
		[dict(c) for c in counters if c["comparison_eligible"] and c["refund_activity_percent"] is not None],
		key=lambda c: c["refund_activity_percent"],
		reverse=True,
	)
	return dict(
		highest_refund_counters=ranked_counters[:5],
		date=str(p["date"]),
		counters=counters,
		large_refunds=large,
		large_refund_threshold=threshold,
		margins=margins,
		voids=voids,
		void_coverage="Voids reported by POS tills (POS Void Log), valued at qty x rate before tax. Tills older than the void-log release report none, and an offline till sends its voids when it reconnects.",
		submitted_cancellations=cancelled,
		total_refunds=round(sum(flt(c.refunds) for c in counters), 2),
		refund_count=sum(c.refund_count for c in counters),
		gross_sales=round(sum(flt(c.gross_sales) for c in counters), 2),
	)


def accounts(branch, business_date):
	p = dict(branch=branch.name, company=branch.company, date=getdate(business_date))
	dues = sql(
		"""SELECT supplier,currency,COUNT(*) bills,SUM(outstanding_amount) outstanding,
        SUM(IF(due_date<%(date)s,outstanding_amount,0)) overdue,MIN(due_date) earliest_due
        FROM `tabPurchase Invoice` WHERE docstatus=1 AND company=%(company)s AND branch=%(branch)s
          AND ABS(outstanding_amount)>0.005 GROUP BY supplier,currency ORDER BY ABS(SUM(outstanding_amount)) DESC""",
		p,
	)
	unbilled = sql(
		"""SELECT supplier,COUNT(*) receipts,SUM(base_net_total*(100-per_billed)/100) estimated_unbilled_net
        FROM `tabPurchase Receipt` WHERE docstatus=1 AND company=%(company)s AND branch=%(branch)s
          AND is_return=0 AND per_billed<100 GROUP BY supplier""",
		p,
	)
	return dict(
		as_of=str(p["date"]),
		supplier_dues=dues,
		received_unbilled=unbilled,
		note="Outstanding balances are current, in invoice currency. Unbilled net is a receipt-based estimate, not posted unpaid bills. Company-only balances are not allocated to branches.",
		cash_reconciliation="Unavailable: independent opening/closing/settlement reconciliation not yet configured.",
	)
