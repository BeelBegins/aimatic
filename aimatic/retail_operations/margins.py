"""Screen recorded costs for review; never replace accounting cost with an estimate."""

from collections import defaultdict

HIGH_MARGIN_PERCENT = 60
COST_VARIANCE_PERCENT = 50


def summarize(rows, stock, receipts, receipt_limited=False):
	groups = defaultdict(list)
	for row in rows:
		groups[row["item_code"]].append(row)
	items = []
	pending = 0
	for code, lines in groups.items():
		sales = [r for r in lines if not r["is_return"]]
		returns = [r for r in lines if r["is_return"]]
		missing = sum(not r.get("has_ledger_cogs") for r in lines)
		pending += missing
		flags = []
		if missing:
			flags.append("Missing or pending ledger cost; item excluded from rankings")
		for row in lines:
			if not row.get("has_ledger_cogs"):
				continue
			cost = float(row["cogs"])
			if cost == 0 and float(row["sales"]) != 0:
				flags.append("Zero recorded cost; verify valuation")
			if (not row["is_return"] and cost < 0) or (row["is_return"] and cost > 0):
				flags.append("Cost sign conflicts with sale/return direction")
			movement = stock.get(row.get("consolidated_item"), {})
			if not row["is_return"] and movement.get("minimum_balance", 0) < 0:
				flags.append("Sale crossed negative stock; verify valuation")
			receipt = receipts.get(code)
			qty = abs(float(row.get("stock_qty") or 0))
			if receipt and receipt["unit_cost"] > 0 and qty:
				variance = abs(abs(cost) / qty - receipt["unit_cost"]) / receipt["unit_cost"] * 100
				if variance > COST_VARIANCE_PERCENT:
					flags.append("Recorded unit cost differs by over 50% from last branch receipt")
		amount = sum(float(r["sales"]) for r in sales)
		cost = sum(float(r.get("cogs") or 0) for r in sales)
		refund = sum(abs(float(r["sales"])) for r in returns)
		reversal = -sum(float(r.get("cogs") or 0) for r in returns)
		profit = round(amount - cost, 2) if not missing else None
		percent = round(profit / abs(amount) * 100, 2) if profit is not None and amount else None
		if sales and amount <= 0:
			flags.append("Non-positive sale value; percentage ranking is not meaningful")
		if percent is not None and percent > HIGH_MARGIN_PERCENT:
			flags.append("Margin above 60%; verify cost before ranking")
		receipt = receipts.get(code, {})
		items.append(
			dict(
				item_code=code,
				item_name=lines[0].get("item_name"),
				sales=round(amount, 2),
				cogs=round(cost, 2) if not missing else None,
				partial_recorded_cogs=round(cost, 2),
				qty=round(sum(float(r.get("stock_qty") or 0) for r in sales), 3),
				profit=profit,
				margin_percent=percent,
				flags=list(dict.fromkeys(flags)),
				return_lines=len(returns),
				refund_sales=round(refund, 2),
				return_qty=round(sum(abs(float(r.get("stock_qty") or 0)) for r in returns), 3),
				cost_reversal=round(reversal, 2) if not missing else None,
				net_sales=round(amount - refund, 2),
				net_profit=round(amount - refund - cost + reversal, 2) if not missing else None,
				receipt_unit_cost=receipt.get("unit_cost"),
				receipt_date=receipt.get("date"),
				sale_lines=len(sales),
				missing_cost_lines=missing,
			)
		)
	eligible = sorted(
		(r for r in items if r["sale_lines"] and not r["flags"] and r["margin_percent"] is not None),
		key=lambda r: (r["margin_percent"], r["item_code"]),
	)
	positive = [r for r in eligible if r["profit"] >= 0]
	exceptions = sorted(
		(r for r in items if r["flags"]),
		key=lambda r: (not any("Zero recorded" in f for f in r["flags"]), -abs(r["sales"]), r["item_code"]),
	)
	return dict(
		status="Review required" if exceptions else "Screened recorded costs",
		pending_cost_lines=pending,
		exception_count=len(exceptions),
		eligible_count=len(eligible),
		exceptions=exceptions,
		negative=[r for r in eligible if r["profit"] < 0][:100],
		lowest=positive[:10],
		highest=list(reversed(positive[-10:])),
		returns=sorted((r for r in items if r["return_lines"]), key=lambda r: -r["refund_sales"]),
		receipt_comparison_limited=receipt_limited,
		note="Sale-only rankings use recorded costs excluding tax. Zero/wrong-sign costs, sales crossing negative stock, margins above 60%, and costs differing over 50% from the latest branch receipt within 90 days are held for review. These are screening rules, not proof of error or audited profit. Lowest excludes negative margins. Returns and their recorded net effect are shown separately."
		+ (
			" Receipt comparison was unavailable because its evidence limit was exceeded."
			if receipt_limited
			else ""
		),
	)
