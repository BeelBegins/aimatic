"""S7 Empire Heights prices + opening stock on siezal (mock).

Source: Itemonhand-S7 Store.xls
- SalePrice (as-is, tax-inclusive shelf) -> S7 Selling Price List
- Onhand + tax-exclusive CurCost -> warehouse S7 - Empire Heights - SSM

Hard-target siezal. Does not create Items. CurCost is tax-inclusive.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s7_prices_and_stock.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)  # post on siezal
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import frappe

TARGET_SITE = "szl"
# 2026-09-06: fresh stock/price file (replaces the stale 2026-09-04 one).
S7_CSV = "/tmp/s7_xls_convert_fresh/1007-Sheet1.csv"
WAREHOUSE = "S7 - Empire Heights - SSM"
PRICE_LIST = "S7 - Empire Heights Selling Price List"
UOM = "Pcs"
REQUIRED_CURRENCY = "PKR"
POSTING_DATE = "2026-09-06"
STOCK_ENTRY_CHUNK_SIZE = 200
POSITIVE_TAG = "S7-ONHAND-IMPORT-2026-09-06"
NEGATIVE_TAG = "S7-ONHAND-NEGATIVE-IMPORT-2026-09-06"
COMMIT_EVERY = 250


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")


def _norm_code(v) -> str:
	if v is None:
		return ""
	s = str(v).strip()
	if s.endswith(".0") and s.replace(".", "", 1).isdigit():
		s = s[:-2]
	return s


def _variants(code: str) -> set[str]:
	out = {code, code.upper(), code.lower()}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for n in (12, 13, 14):
			out.add(code.zfill(n))
			out.add(stripped.zfill(n))
	return {c for c in out if c}


def _as_float(v) -> float:
	try:
		return float(str(v or "").strip() or 0)
	except ValueError:
		return 0.0


def exclusive_rate(inclusive_rate, tax_rate):
	if inclusive_rate <= 0 or tax_rate <= 0:
		return inclusive_rate
	sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
	return round(inclusive_rate - sales_tax, 2)


def parse_s7_rows():
	path = Path(S7_CSV)
	if not path.is_file():
		frappe.throw(f"S7 CSV missing: {S7_CSV}")
	rows = []
	with path.open(newline="", encoding="utf-8", errors="replace") as f:
		reader = csv.reader(f)
		next(reader, None)
		for i, row in enumerate(reader, start=2):
			if not row or not any(str(c).strip() for c in row):
				continue
			while len(row) < 7:
				row.append("")
			b1, b2 = _norm_code(row[0]), _norm_code(row[5])
			if not b1 and not b2:
				continue
			rows.append(
				{
					"row": i,
					"Barcode1": b1,
					"Barcode2": b2,
					"Description": str(row[1] or "").strip(),
					"Onhand": _as_float(row[2]),
					"CurCost": _as_float(row[3]),
					"SalePrice": _as_float(row[4]),
				}
			)
	return rows


def barcode_index():
	idx = defaultdict(set)
	for barcode, parent in frappe.db.sql(
		"SELECT barcode, parent FROM `tabItem Barcode` WHERE barcode IS NOT NULL AND barcode != ''"
	):
		code = str(barcode).strip()
		if not code:
			continue
		for v in _variants(code):
			idx[v].add(parent)
	return idx


def resolve_item(row, idx):
	matched = set()
	for code in (row["Barcode1"], row["Barcode2"]):
		if not code:
			continue
		for v in _variants(code):
			matched.update(idx.get(v, set()))
	return matched


def get_item_tax_rate(item_code, cache):
	if item_code not in cache:
		category = frappe.db.get_value("Item", item_code, "custom_fbr_tax_category")
		cache[item_code] = (
			_as_float(frappe.db.get_value("FBR Tax Category", category, "tax_rate")) if category else 0.0
		)
	return cache[item_code]


def get_branch_and_cost_center():
	branch = frappe.get_cached_value("Warehouse", WAREHOUSE, "custom_branch")
	if not branch:
		frappe.throw(f"Warehouse {WAREHOUSE} has no Branch mapped.")
	cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
	if not cost_center:
		frappe.throw(f"Branch {branch} has no Cost Center.")
	return branch, cost_center


def get_temp_opening_account():
	company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
	account = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
	)
	if not account:
		frappe.throw(f"No Temporary Opening account for {company}")
	return account


def build_plan(rows):
	idx = barcode_index()
	tax_cache = {}
	disabled = set(frappe.get_all("Item", filters={"disabled": 1}, pluck="name"))
	stats = Counter()
	issues = []
	by_item = {}

	for row in rows:
		stats["source_rows"] += 1
		matched = resolve_item(row, idx)
		if len(matched) != 1:
			key = "unmatched" if not matched else "ambiguous"
			stats[key] += 1
			issues.append(
				{
					"row": row["row"],
					"b1": row["Barcode1"],
					"desc": row["Description"][:60],
					"reason": key,
					"items": sorted(matched),
				}
			)
			continue
		item_code = next(iter(matched))
		if item_code in disabled:
			stats["disabled"] += 1
			issues.append({"row": row["row"], "b1": row["Barcode1"], "reason": "disabled", "items": [item_code]})
			continue
		tax_rate = get_item_tax_rate(item_code, tax_cache)
		excl = exclusive_rate(row["CurCost"], tax_rate)
		if item_code in by_item:
			stats["merged_duplicate_item"] += 1
			prev = by_item[item_code]
			prev["onhand"] += row["Onhand"]
			if row["SalePrice"] > 0 and prev["sale_price"] <= 0:
				prev["sale_price"] = row["SalePrice"]
			# keep first exclusive rate unless previous cost was 0
			if prev["exclusive_cost"] <= 0 and excl:
				prev["exclusive_cost"] = excl
				prev["inclusive_cost"] = row["CurCost"]
				prev["tax_rate"] = tax_rate
			continue
		by_item[item_code] = {
			"item_code": item_code,
			"onhand": row["Onhand"],
			"sale_price": row["SalePrice"],
			"inclusive_cost": row["CurCost"],
			"exclusive_cost": excl,
			"tax_rate": tax_rate,
			"description": row["Description"],
		}

	prices, pos, neg, zero_qty = [], [], [], 0
	for item_code, rec in by_item.items():
		if rec["sale_price"] > 0:
			prices.append(rec)
		else:
			stats["zero_saleprice"] += 1
		if rec["onhand"] > 0:
			pos.append(
				{"item_code": item_code, "qty": rec["onhand"], "rate": rec["exclusive_cost"], "incl": rec["inclusive_cost"]}
			)
		elif rec["onhand"] < 0:
			neg.append(
				{
					"item_code": item_code,
					"qty": abs(rec["onhand"]),
					"rate": rec["exclusive_cost"],
					"incl": rec["inclusive_cost"],
				}
			)
		else:
			zero_qty += 1
	stats["resolved_items"] = len(by_item)
	stats["price_rows"] = len(prices)
	stats["positive_stock"] = len(pos)
	stats["negative_stock"] = len(neg)
	stats["zero_onhand"] = zero_qty
	incl_value = sum(r["onhand"] * r["inclusive_cost"] for r in by_item.values())
	excl_value = sum(p["qty"] * p["rate"] for p in pos) - sum(n["qty"] * n["rate"] for n in neg)
	return {
		"stats": stats,
		"issues": issues,
		"prices": prices,
		"pos": pos,
		"neg": neg,
		"inclusive_onhand_x_curcost": round(incl_value, 2),
		"exclusive_signed_stock_value": round(excl_value, 2),
	}


def apply_prices(prices):
	pl = frappe.get_doc("Price List", PRICE_LIST)
	if not pl.enabled or not pl.selling:
		frappe.throw(f"Unexpected Price List {PRICE_LIST}")
	if pl.currency != REQUIRED_CURRENCY:
		frappe.throw(f"Price List {PRICE_LIST} currency is {pl.currency}, expected {REQUIRED_CURRENCY}")
	currency = REQUIRED_CURRENCY
	existing = {}
	for name, item_code, rate in frappe.db.sql(
		"SELECT name, item_code, price_list_rate FROM `tabItem Price` WHERE price_list=%s ORDER BY creation",
		PRICE_LIST,
	):
		existing.setdefault(item_code, {"name": name, "rate": float(rate or 0)})
	changed = 0
	for rec in prices:
		item_code = rec["item_code"]
		rate = rec["sale_price"]
		if item_code in existing:
			name, old = existing[item_code]["name"], existing[item_code]["rate"]
			if abs(old - rate) < 0.005:
				continue
			frappe.db.set_value("Item Price", name, "price_list_rate", rate)
			existing[item_code]["rate"] = rate
		else:
			try:
				price = frappe.new_doc("Item Price")
				price.item_code = item_code
				price.price_list = PRICE_LIST
				price.price_list_rate = rate
				price.uom = UOM
				price.currency = currency
				price.selling = 1
				price.insert(ignore_permissions=True)
				existing[item_code] = {"name": price.name, "rate": rate}
			except Exception:
				frappe.db.rollback()
				# Race / leftover duplicate from the interrupted pass.
				hit = frappe.db.get_value(
					"Item Price",
					{"item_code": item_code, "price_list": PRICE_LIST},
					["name", "price_list_rate"],
					as_dict=True,
				)
				if not hit:
					raise
				existing[item_code] = {"name": hit.name, "rate": float(hit.price_list_rate or 0)}
				if abs(existing[item_code]["rate"] - rate) >= 0.005:
					frappe.db.set_value("Item Price", hit.name, "price_list_rate", rate)
					existing[item_code]["rate"] = rate
				else:
					continue
		changed += 1
		if changed % COMMIT_EVERY == 0:
			frappe.db.commit()
	frappe.db.commit()
	return changed


def _wait_for_queue_headroom(max_depth: int = 300, poll_seconds: float = 3.0):
	"""Live szl's `on_bin_update` hook enqueues one Foodpanda sync_availability
	job (queue="short") per Bin touched -- harmless (cheap no-op once no
	Foodpanda Product mapping exists yet) but the queue has a hard cap
	(QueueOverloaded at 750 jobs), and a single "short" worker only drains
	~1/sec. Bulk-posting 200-item chunks back to back can outrun that drain
	rate and blow the cap mid-run (hit live 2026-09-06: S7's Foodpanda Outlet
	now has catalog_sync_enabled=1, so every S7 Bin update queues a real job).
	Throttle here rather than let entry.submit() throw and leave a chunk half
	-committed.
	"""
	import time

	from frappe.utils.background_jobs import get_queue

	while True:
		depth = get_queue("short").count
		if depth <= max_depth:
			return
		time.sleep(poll_seconds)


def _submit_chunks(rows, *, positive: bool):
	if not rows:
		return 0
	temp = get_temp_opening_account()
	branch, cost_center = get_branch_and_cost_center()
	tag_base = POSITIVE_TAG if positive else NEGATIVE_TAG
	created = 0
	skipped = 0
	for start in range(0, len(rows), STOCK_ENTRY_CHUNK_SIZE):
		chunk = rows[start : start + STOCK_ENTRY_CHUNK_SIZE]
		chunk_index = start // STOCK_ENTRY_CHUNK_SIZE
		tag = f"{tag_base} chunk {chunk_index}"
		if frappe.db.exists("Stock Entry", {"remarks": tag}):
			skipped += 1
			continue
		_wait_for_queue_headroom()
		entry = frappe.new_doc("Stock Entry")
		entry.posting_date = POSTING_DATE
		entry.set_posting_time = 1
		entry.branch = branch
		entry.cost_center = cost_center
		entry.remarks = tag
		if positive:
			entry.stock_entry_type = "Material Receipt"
			entry.to_warehouse = WAREHOUSE
		else:
			entry.stock_entry_type = "Material Issue"
			entry.from_warehouse = WAREHOUSE
			for stock_row in chunk:
				if stock_row["rate"] > 0:
					frappe.db.set_value("Item", stock_row["item_code"], "valuation_rate", stock_row["rate"])
			frappe.db.commit()
		for stock_row in chunk:
			line = {
				"item_code": stock_row["item_code"],
				"qty": stock_row["qty"],
				"basic_rate": stock_row["rate"],
				"allow_zero_valuation_rate": 1 if stock_row["rate"] <= 0 else 0,
				"expense_account": temp,
				"cost_center": cost_center,
				"branch": branch,
			}
			if positive:
				line["t_warehouse"] = WAREHOUSE
				line["valuation_rate"] = stock_row["rate"]
			else:
				line["s_warehouse"] = WAREHOUSE
			entry.append("items", line)
		entry.insert(ignore_permissions=True)
		entry.submit()
		blank_rows = frappe.db.sql(
			"""
			select count(*) from `tabStock Entry Detail`
			where parent=%s and ifnull(cost_center,'')=''
			""",
			entry.name,
		)[0][0]
		blank_gl = frappe.db.sql(
			"""
			select count(*) from `tabGL Entry`
			where voucher_no=%s and voucher_type='Stock Entry'
			  and ifnull(cost_center,'')=''
			""",
			entry.name,
		)[0][0]
		if blank_rows or blank_gl:
			frappe.throw(
				f"{entry.name} posted without cost center: blank_rows={blank_rows} blank_gl={blank_gl}"
			)
		frappe.db.commit()
		created += 1
		print(f"Posted {entry.name} {tag} rows={len(chunk)}")
	print(f"{'positive' if positive else 'negative'}: created={created} skipped_existing={skipped}")
	return created


def verify_bins(pos, neg):
	expected = {r["item_code"]: r["qty"] for r in pos}
	for r in neg:
		expected[r["item_code"]] = -r["qty"]
	missing = []
	mismatched = []
	for item_code, qty in expected.items():
		actual = float(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": WAREHOUSE}, "actual_qty") or 0
		)
		if abs(actual - qty) > 0.02:
			if actual == 0:
				missing.append((item_code, qty, actual))
			else:
				mismatched.append((item_code, qty, actual))
	bin_value = frappe.db.sql(
		"select round(sum(stock_value),2) from tabBin where warehouse=%s", WAREHOUSE
	)[0][0]
	return {
		"expected_rows": len(expected),
		"missing_or_zero_bin": len(missing),
		"qty_mismatch": len(mismatched),
		"mismatch_sample": mismatched[:10] + missing[:10],
		"bin_stock_value": bin_value,
	}


def main(apply: bool = False):
	_assert_site()
	if frappe.db.count("Bin", {"warehouse": WAREHOUSE}) and apply:
		# allow re-run only via chunk tags; warn if bins already exist from another source
		existing_tags = frappe.db.exists("Stock Entry", {"remarks": ["like", f"{POSITIVE_TAG}%"]})
		if not existing_tags:
			nonzero = frappe.db.sql(
				"select count(*) from tabBin where warehouse=%s and actual_qty!=0", WAREHOUSE
			)[0][0]
			if nonzero:
				frappe.throw(
					f"Warehouse {WAREHOUSE} already has {nonzero} nonzero bins; refusing to post opening."
				)
	rows = parse_s7_rows()
	plan = build_plan(rows)
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"warehouse": WAREHOUSE,
			"price_list": PRICE_LIST,
			"posting_date": POSTING_DATE,
			"stats": dict(plan["stats"]),
			"inclusive_onhand_x_curcost": plan["inclusive_onhand_x_curcost"],
			"exclusive_signed_stock_value": plan["exclusive_signed_stock_value"],
			"issue_counts": Counter(i["reason"] for i in plan["issues"]),
			"issue_sample": plan["issues"][:15],
		}
	)
	if not apply:
		print("DRY_RUN — no prices or stock posted.")
		return plan["stats"]

	price_changes = apply_prices(plan["prices"])
	print("price_changes", price_changes)
	pos_entries = _submit_chunks(plan["pos"], positive=True)
	neg_entries = _submit_chunks(plan["neg"], positive=False)
	bins = verify_bins(plan["pos"], plan["neg"])
	pl_count = frappe.db.count("Item Price", {"price_list": PRICE_LIST})
	print(
		{
			"price_changes": price_changes,
			"price_list_rows": pl_count,
			"pos_entries": pos_entries,
			"neg_entries": neg_entries,
			"bin_check": bins,
		}
	)
	return {"price_changes": price_changes, "bins": bins}
