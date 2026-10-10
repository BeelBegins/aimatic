"""S2 Bhatta Chowk selling prices and opening stock.

Source: s2ItemMasterho.xlsx (one file: master + S2 stock/price columns).
- Slprice -> S2 - Bhatta Chowk Selling Price List (see PRICE_SCOPE)
- Onhand (signed) + tax-exclusive CurCost -> S2 - Bhatta Chowk - SSM

CurCost is tax-INCLUSIVE (user, 2026-10-11): it is reverse-calculated with the
Item's own FBR Tax Category rate (import.md "Tax-exclusive valuation rate")
before use as valuation. Positive Onhand posts as Material Receipt, negative as
Material Issue; both are in scope. Rows with Onhand == 0 are ignored.

User decisions 2026-10-11 reflected here:
- Items missing from szl are created first (create_s2_missing_items.py); rows
  whose SubCatName is junk (blank/NULL/TESTING) are excluded, like S6.
- HELD, not posted: rows whose two barcodes resolve to two different Items, and
  Items hit by more than one file row. They are reported with their value and
  handled at the very end.
- Fbr_Tax_Category is the tax source (Tax_Rate column only cross-checked).

Hard-targets live ``szl``. Dry-run by default. Apply needs a verified backup,
a set PRICE_SCOPE, and zero blocking issues. While pending Items are not yet
created, their rows show as ``pending_create`` and block apply.

Run from ``bench --site szl execute`` / console with one explicit namespace:

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_prices_and_stock.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)  # live apply, only after backup + approval
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from pathlib import Path

import frappe
from openpyxl import load_workbook

TARGET_SITE = "szl"
SOURCE = "/home/nabeel/frappe-bench/sites/szl/private/files/s2ItemMasterho.xlsx"
SOURCE_SHA256 = "9d5292e5aa74465e35adaa16bd5456d9f930fd597bf75ee4522b7cc1512ff482"
EXPECTED_SOURCE_ROWS = 41552
EXPECTED_SIGNED_INCLUSIVE_VALUE = 40792497.29  # sum of Stock_Value, verified 2026-10-11

COMPANY = "Siezal Supermarket"
BRANCH = "S2 - Bhatta Chowk"
WAREHOUSE = "S2 - Bhatta Chowk - SSM"
COST_CENTER = "S2 - Bhatta Chowk - SSM"
PRICE_LIST = "S2 - Bhatta Chowk Selling Price List"
UOM = "Pcs"
REQUIRED_CURRENCY = "PKR"
POSTING_DATE = "2026-10-11"  # PENDING USER CONFIRMATION before apply

# Which matched Items get a selling price: "stock_only" (Onhand != 0 rows only)
# or "all_matched" (every resolved row with Slprice > 0). User 2026-10-11:
# "price just <> 0 waly items ka" -> every resolved Item whose Slprice is nonzero.
PRICE_SCOPE = "all_matched"

STOCK_ENTRY_CHUNK_SIZE = 200
PRICE_COMMIT_EVERY = 250
POSITIVE_TAG = "S2-ONHAND-IMPORT-2026-10-11"
NEGATIVE_TAG = "S2-ONHAND-NEGATIVE-IMPORT-2026-10-11"
JUNK_SUBCATS = {"", "NULL", "TESTING"}


def _assert_site_and_source():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing S2 price/stock import: expected site {TARGET_SITE}, got {site}")
	path = Path(SOURCE)
	if not path.is_file():
		frappe.throw(f"S2 source missing: {SOURCE}")
	digest = hashlib.sha256(path.read_bytes()).hexdigest()
	if digest != SOURCE_SHA256:
		frappe.throw(f"S2 source hash changed: expected {SOURCE_SHA256}, got {digest}")


def _norm(v) -> str:
	if v is None:
		return ""
	s = str(v).strip()
	if s.endswith(".0") and s.replace(".", "", 1).isdigit():
		s = s[:-2]
	return "" if s.upper() == "NULL" else s


def _num(v) -> float:
	try:
		return float(str(v or "").strip() or 0)
	except (TypeError, ValueError):
		return 0.0


def _variants(code: str) -> set[str]:
	out = {code, code.upper(), code.lower()}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for width in (12, 13, 14):
			out.add(code.zfill(width))
			out.add(stripped.zfill(width))
	return {v for v in out if v}


def parse_rows() -> list[dict]:
	ws = load_workbook(SOURCE, read_only=True, data_only=True).active
	it = ws.iter_rows(values_only=True)
	header = [str(h).strip() if h is not None else "" for h in next(it)]
	rows = []
	for n, raw in enumerate(it, start=2):
		if not any(v is not None for v in raw):
			continue
		rec = {header[i]: (raw[i] if i < len(raw) else None) for i in range(len(header))}
		rows.append(
			{
				"row": n,
				"Barcode1": _norm(rec.get("ItemCode")),
				"Barcode2": _norm(rec.get("RefCode")),
				"Description": _norm(rec.get("Description")),
				"SubCatName": _norm(rec.get("SubCatName")),
				"Onhand": _num(rec.get("Onhand")),
				"CurCost": _num(rec.get("CurCost")),
				"SalePrice": _num(rec.get("Slprice")),
				"FbrCategory": _norm(rec.get("Fbr_Tax_Category")),
				"FileTaxRate": _num(rec.get("Tax_Rate")),
				"StockValue": _num(rec.get("Stock_Value")),
			}
		)
	if len(rows) != EXPECTED_SOURCE_ROWS:
		frappe.throw(f"Unexpected S2 source row count: expected {EXPECTED_SOURCE_ROWS}, got {len(rows)}")
	return rows


def _assert_accounting_context():
	wh = frappe.db.get_value("Warehouse", WAREHOUSE, ["company", "custom_branch"], as_dict=True)
	if not wh:
		frappe.throw(f"Missing warehouse {WAREHOUSE}")
	if wh.company != COMPANY or wh.custom_branch != BRANCH:
		frappe.throw(f"Unexpected warehouse context: company={wh.company}, branch={wh.custom_branch}")
	cc = frappe.db.get_value("Branch", BRANCH, "cost_center")
	if cc != COST_CENTER:
		frappe.throw(f"Unexpected S2 cost center: expected {COST_CENTER}, got {cc}")
	pl = frappe.db.get_value("Price List", PRICE_LIST, ["enabled", "selling", "currency"], as_dict=True)
	if not pl or not pl.enabled or not pl.selling:
		frappe.throw(f"Missing or disabled Selling Price List {PRICE_LIST}")
	if pl.currency != REQUIRED_CURRENCY:
		frappe.throw(f"Unexpected {PRICE_LIST} currency: {pl.currency}")
	if frappe.db.get_single_value("Stock Settings", "valuation_method") != "Moving Average":
		frappe.throw("Stock Settings valuation_method must be Moving Average")
	if int(frappe.db.get_value("UOM", UOM, "must_be_whole_number") or 0):
		frappe.throw(f"UOM {UOM} must allow fractional quantities")


def barcode_indexes():
	barcode_exact, item_exact, variant = defaultdict(set), defaultdict(set), defaultdict(set)
	for barcode, parent in frappe.db.sql(
		"SELECT barcode, parent FROM `tabItem Barcode` WHERE IFNULL(barcode, '') != ''"
	):
		code = str(barcode).strip()
		if code:
			barcode_exact[code.lower()].add(parent)
			for c in _variants(code):
				variant[c.lower()].add(parent)
	for item_code in frappe.get_all("Item", pluck="name"):
		code = str(item_code).strip()
		item_exact[code.lower()].add(item_code)
		for c in _variants(code):
			variant[c.lower()].add(item_code)
	return barcode_exact, item_exact, variant


def _resolve_code(code, barcode_exact, item_exact, variant) -> set[str]:
	if not code:
		return set()
	if barcode_exact.get(code.lower()):
		return set(barcode_exact[code.lower()])
	if item_exact.get(code.lower()):
		return set(item_exact[code.lower()])
	matches = set()
	for c in _variants(code):
		matches.update(variant.get(c.lower(), set()))
	return matches


def exclusive_rate(inclusive_rate: float, tax_rate: float) -> float:
	if inclusive_rate <= 0 or tax_rate <= 0:
		return inclusive_rate
	return round(inclusive_rate - inclusive_rate * tax_rate / (100 + tax_rate), 2)


def _tax_rate_for_category(category: str, cache: dict):
	if category in cache:
		return cache[category]
	rate = None
	if category and frappe.db.exists("FBR Tax Category", category):
		rate = _num(frappe.db.get_value("FBR Tax Category", category, "tax_rate"))
	cache[category] = rate
	return rate


def build_plan(rows):
	be, ie, vr = barcode_indexes()
	cat_cache, item_cat_cache = {}, {}
	disabled = set(frappe.get_all("Item", filters={"disabled": 1}, pluck="name"))
	stats, issues, warnings = Counter(), [], []
	staged = []  # resolved rows before the double-hit hold
	pending = Counter()
	pending_incl = pending_excl = 0.0
	held_two_item = held_incl = 0.0
	junk_incl = 0.0
	zero_stock_incl = 0.0
	all_incl = 0.0

	for r in rows:
		stats["source_rows"] += 1
		all_incl += r["Onhand"] * r["CurCost"]
		if not r["Onhand"]:
			stats["zero_stock_ignored"] += 1
			zero_stock_incl += r["Onhand"] * r["CurCost"]
			if PRICE_SCOPE != "stock_only":
				# kept for price planning only
				pass
		p1 = _resolve_code(r["Barcode1"], be, ie, vr)
		p2 = _resolve_code(r["Barcode2"], be, ie, vr)
		all1 = p1 | p2
		if len(all1) > 1 and p1 and p2 and p1 != p2:
			if r["Onhand"]:
				stats["held_two_item_conflict"] += 1
				held_two_item += 1
				held_incl += r["Onhand"] * r["CurCost"]
			continue
		if len(p1) > 1 or (not p1 and len(p2) > 1):
			if r["Onhand"]:
				stats["held_ambiguous"] += 1
				held_incl += r["Onhand"] * r["CurCost"]
			continue
		matches = p1 or p2
		if not matches:
			if not r["Onhand"]:
				continue
			if r["SubCatName"].upper() in JUNK_SUBCATS:
				stats["junk_subcat_excluded"] += 1
				junk_incl += r["Onhand"] * r["CurCost"]
				continue
			rate = _tax_rate_for_category(r["FbrCategory"], cat_cache)
			if rate is None:
				issues.append({"row": r["row"], "reason": "pending_create_unknown_fbr", "barcode1": r["Barcode1"]})
				continue
			stats["pending_create"] += 1
			pending_incl += r["Onhand"] * r["CurCost"]
			pending_excl += r["Onhand"] * exclusive_rate(r["CurCost"], rate)
			issues.append({"row": r["row"], "reason": "pending_create", "barcode1": r["Barcode1"]})
			continue
		item_code = next(iter(matches))
		stats["barcode1" if p1 else "barcode2_fallback"] += 1
		staged.append({**r, "item": item_code})

	rows_per_item = defaultdict(list)
	for s in staged:
		rows_per_item[s["item"]].append(s)
	by_item, prices = {}, []
	held_double_items = held_double_incl = 0
	for item, rs in rows_per_item.items():
		if len(rs) > 1 and any(x["Onhand"] for x in rs):
			stats["held_double_hit_items"] += 1
			stats["held_double_hit_rows"] += len(rs)
			held_double_items += 1
			held_double_incl += sum(x["Onhand"] * x["CurCost"] for x in rs)
			continue
		x = rs[0]
		if item in disabled:
			if x["Onhand"]:
				stats["disabled"] += 1
				issues.append({"row": x["row"], "reason": "disabled", "items": [item]})
			continue
		want_price = x["SalePrice"] > 0 and (
			PRICE_SCOPE == "all_matched" or (x["Onhand"] and PRICE_SCOPE in ("stock_only", "all_matched", None))
		)
		if x["SalePrice"] > 0:
			stats["price_candidates_all_matched"] += 1
			if x["Onhand"]:
				stats["price_candidates_stock_only"] += 1
		if want_price:
			prices.append({"item_code": item, "sale_price": round(x["SalePrice"], 2)})
		if not x["Onhand"]:
			continue
		cat = frappe.db.get_value("Item", item, "custom_fbr_tax_category")
		rate = _tax_rate_for_category(cat, cat_cache)
		if rate is None:
			stats["missing_fbr_category"] += 1
			issues.append({"row": x["row"], "reason": "missing_fbr_category", "items": [item]})
			continue
		if x["FbrCategory"] and x["FbrCategory"] != cat:
			stats["item_vs_file_tax_category_differs"] += 1
			warnings.append({"row": x["row"], "item": item, "item_cat": cat, "file_cat": x["FbrCategory"]})
		by_item[item] = {
			"item_code": item,
			"qty": x["Onhand"],
			"rate": exclusive_rate(x["CurCost"], rate),
			"inclusive_value": x["Onhand"] * x["CurCost"],
			"row": x["row"],
		}

	positive, negative = [], []
	for item, rec in by_item.items():
		row = {
			"item_code": item,
			"qty": abs(rec["qty"]),
			"signed_qty": rec["qty"],
			"rate": rec["rate"],
			"expected_value": round(rec["qty"] * rec["rate"], 6),
			"expected_inclusive_value": round(rec["inclusive_value"], 6),
		}
		(positive if rec["qty"] > 0 else negative).append(row)

	stats["resolved_stock_items"] = len(by_item)
	stats["positive_stock"] = len(positive)
	stats["negative_stock"] = len(negative)
	stats["price_rows_planned"] = len(prices)
	stats["blocking_issues"] = len(issues)
	post_incl = round(sum(r["expected_inclusive_value"] for r in positive + negative), 2)
	post_excl = round(sum(r["expected_value"] for r in positive + negative), 2)
	return {
		"stats": stats,
		"issues": issues,
		"warnings": warnings,
		"prices": prices,
		"positive": positive,
		"negative": negative,
		"reconciliation": {
			"file_signed_inclusive_total": round(all_incl, 2),
			"expected_file_total": EXPECTED_SIGNED_INCLUSIVE_VALUE,
			"resolved_now_inclusive": post_incl,
			"resolved_now_exclusive": post_excl,
			"pending_create_inclusive": round(pending_incl, 2),
			"pending_create_exclusive_estimate": round(pending_excl, 2),
			"held_conflict_or_ambiguous_inclusive": round(held_incl, 2),
			"held_double_hit_inclusive": round(held_double_incl, 2),
			"junk_subcat_excluded_inclusive": round(junk_incl, 2),
			"parts_sum_inclusive": round(
				post_incl + pending_incl + held_incl + held_double_incl + junk_incl, 2
			),
		},
	}


def _existing_prices():
	rows = frappe.db.sql(
		"SELECT name, item_code, price_list_rate FROM `tabItem Price` WHERE price_list=%s ORDER BY creation",
		PRICE_LIST,
		as_dict=True,
	)
	by_item = defaultdict(list)
	for row in rows:
		by_item[row.item_code].append(row)
	dups = {i: v for i, v in by_item.items() if len(v) > 1}
	if dups:
		frappe.throw(f"Duplicate Item Prices already exist in {PRICE_LIST}: {list(dups)[:10]}")
	return {i: v[0] for i, v in by_item.items()}


def apply_prices(prices):
	existing = _existing_prices()
	changed = 0
	for rec in prices:
		current = existing.get(rec["item_code"])
		if current and abs(float(current.price_list_rate or 0) - rec["sale_price"]) < 0.005:
			continue
		if current:
			frappe.db.set_value("Item Price", current.name, "price_list_rate", rec["sale_price"])
		else:
			price = frappe.new_doc("Item Price")
			price.item_code = rec["item_code"]
			price.price_list = PRICE_LIST
			price.price_list_rate = rec["sale_price"]
			price.uom = UOM
			price.currency = REQUIRED_CURRENCY
			price.selling = 1
			price.insert(ignore_permissions=True)
		changed += 1
		if changed % PRICE_COMMIT_EVERY == 0:
			frappe.db.commit()
	frappe.db.commit()
	return changed


def _temporary_opening_account():
	account = frappe.db.get_value(
		"Account", {"company": COMPANY, "account_name": "Temporary Opening", "is_group": 0}
	)
	if not account:
		frappe.throw(f"No Temporary Opening account for {COMPANY}")
	return account


def _wait_for_queue_headroom(max_depth: int = 300, poll_seconds: float = 3.0):
	from frappe.utils.background_jobs import get_queue

	while get_queue("short").count > max_depth:
		time.sleep(poll_seconds)


def _expected_chunk_tags(rows, *, positive: bool) -> list[str]:
	base = POSITIVE_TAG if positive else NEGATIVE_TAG
	return [f"{base} chunk {s // STOCK_ENTRY_CHUNK_SIZE}" for s in range(0, len(rows), STOCK_ENTRY_CHUNK_SIZE)]


def _assert_opening_state(plan):
	expected = set(_expected_chunk_tags(plan["positive"], positive=True)) | set(
		_expected_chunk_tags(plan["negative"], positive=False)
	)
	existing = frappe.db.sql(
		"SELECT name, remarks, docstatus FROM `tabStock Entry` WHERE remarks LIKE 'S2-ONHAND-%'", as_dict=True
	)
	unexpected = [r for r in existing if r.remarks not in expected]
	if unexpected:
		frappe.throw(f"Unexpected S2 opening tags already exist: {unexpected[:10]}")
	dup = {t: c for t, c in Counter(r.remarks for r in existing if r.docstatus == 1).items() if c > 1}
	if dup:
		frappe.throw(f"Duplicate submitted S2 opening tags: {dup}")
	nonzero = frappe.db.sql("SELECT COUNT(*) FROM tabBin WHERE warehouse=%s AND actual_qty != 0", WAREHOUSE)[0][0]
	if nonzero and not any(r.docstatus == 1 for r in existing):
		frappe.throw(f"{WAREHOUSE} already has {nonzero} nonzero bins without S2 opening tags")


def _submit_chunks(rows, *, positive: bool):
	if not rows:
		return {"created": 0, "skipped": 0}
	account = _temporary_opening_account()
	created = skipped = 0
	tags = _expected_chunk_tags(rows, positive=positive)
	for start in range(0, len(rows), STOCK_ENTRY_CHUNK_SIZE):
		chunk = rows[start : start + STOCK_ENTRY_CHUNK_SIZE]
		tag = tags[start // STOCK_ENTRY_CHUNK_SIZE]
		if frappe.db.exists("Stock Entry", {"remarks": tag, "docstatus": 1}):
			skipped += 1
			continue
		_wait_for_queue_headroom()
		entry = frappe.new_doc("Stock Entry")
		entry.posting_date = POSTING_DATE
		entry.set_posting_time = 1
		entry.company = COMPANY
		entry.branch = BRANCH
		entry.cost_center = COST_CENTER
		entry.remarks = tag
		if positive:
			entry.stock_entry_type = "Material Receipt"
			entry.to_warehouse = WAREHOUSE
		else:
			entry.stock_entry_type = "Material Issue"
			entry.from_warehouse = WAREHOUSE
			for sr in chunk:
				if sr["rate"] > 0:
					frappe.db.set_value("Item", sr["item_code"], "valuation_rate", sr["rate"])
			frappe.db.commit()
		for sr in chunk:
			line = {
				"item_code": sr["item_code"],
				"qty": sr["qty"],
				"basic_rate": sr["rate"],
				"allow_zero_valuation_rate": 1 if sr["rate"] <= 0 else 0,
				"expense_account": account,
				"cost_center": COST_CENTER,
				"branch": BRANCH,
			}
			if positive:
				line["t_warehouse"] = WAREHOUSE
				line["valuation_rate"] = sr["rate"]
			else:
				line["s_warehouse"] = WAREHOUSE
			entry.append("items", line)
		entry.insert(ignore_permissions=True)
		entry.submit()
		blank_detail = frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabStock Entry Detail`
			WHERE parent=%s AND (IFNULL(cost_center, '')='' OR IFNULL(branch, '')='')""",
			entry.name,
		)[0][0]
		blank_gl = frappe.db.sql(
			"""SELECT COUNT(*) FROM `tabGL Entry`
			WHERE voucher_type='Stock Entry' AND voucher_no=%s
			  AND (IFNULL(cost_center, '')='' OR IFNULL(branch, '')='')""",
			entry.name,
		)[0][0]
		if blank_detail or blank_gl:
			frappe.throw(f"{entry.name} missing S2 dimensions: detail={blank_detail}, gl={blank_gl}")
		frappe.db.commit()
		created += 1
		print(f"Posted {entry.name} {tag} rows={len(chunk)}")
	return {"created": created, "skipped": skipped}


def verify_prices(prices):
	existing = _existing_prices()
	missing, mismatched = [], []
	for rec in prices:
		actual = existing.get(rec["item_code"])
		if not actual:
			missing.append(rec["item_code"])
		elif abs(float(actual.price_list_rate or 0) - rec["sale_price"]) >= 0.005:
			mismatched.append((rec["item_code"], rec["sale_price"], actual.price_list_rate))
	return {
		"expected": len(prices),
		"price_list_rows": len(existing),
		"missing": len(missing),
		"mismatched": len(mismatched),
		"sample": missing[:10] + mismatched[:10],
	}


def verify_stock(plan):
	expected = {r["item_code"]: r["signed_qty"] for r in plan["positive"] + plan["negative"]}
	missing, mismatched = [], []
	for item_code, qty in expected.items():
		actual = float(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": WAREHOUSE}, "actual_qty") or 0
		)
		if abs(actual - qty) > 0.0001:
			(missing if actual == 0 else mismatched).append((item_code, qty, actual))
	bins = frappe.db.sql(
		"SELECT COUNT(*), ROUND(SUM(stock_value), 2) FROM tabBin WHERE warehouse=%s AND actual_qty != 0",
		WAREHOUSE,
	)[0]
	pat = "S2-ONHAND-%"
	sle = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabStock Entry` se ON se.name=sle.voucher_no
		WHERE sle.voucher_type='Stock Entry' AND se.remarks LIKE %s AND sle.is_cancelled=0""",
		pat,
	)[0][0]
	blank_detail = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabStock Entry Detail` sed INNER JOIN `tabStock Entry` se ON se.name=sed.parent
		WHERE se.docstatus=1 AND se.remarks LIKE %s
		  AND (IFNULL(sed.cost_center, '')='' OR IFNULL(sed.branch, '')='')""",
		pat,
	)[0][0]
	blank_gl = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabGL Entry` gl INNER JOIN `tabStock Entry` se ON se.name=gl.voucher_no
		WHERE gl.voucher_type='Stock Entry' AND se.docstatus=1 AND se.remarks LIKE %s
		  AND (IFNULL(gl.cost_center, '')='' OR IFNULL(gl.branch, '')='')""",
		pat,
	)[0][0]
	expected_value = round(sum(r["expected_value"] for r in plan["positive"] + plan["negative"]), 2)
	return {
		"expected_rows": len(expected),
		"nonzero_bins": int(bins[0] or 0),
		"bin_stock_value": float(bins[1] or 0),
		"expected_stock_value": expected_value,
		"stock_value_delta": round(float(bins[1] or 0) - expected_value, 2),
		"sle_count": sle,
		"missing_or_zero_bin": len(missing),
		"qty_mismatch": len(mismatched),
		"blank_detail_dimensions": blank_detail,
		"blank_gl_dimensions": blank_gl,
		"mismatch_sample": mismatched[:10] + missing[:10],
	}


def main(apply: bool = False):
	_assert_site_and_source()
	_assert_accounting_context()
	plan = build_plan(parse_rows())
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"warehouse": WAREHOUSE,
			"price_list": PRICE_LIST,
			"posting_date": POSTING_DATE,
			"price_scope": PRICE_SCOPE,
			"stats": dict(plan["stats"]),
			"reconciliation": plan["reconciliation"],
			"issue_counts": dict(Counter(i["reason"] for i in plan["issues"])),
			"issue_sample": plan["issues"][:10],
			"warning_sample": plan["warnings"][:10],
		}
	)
	if plan["issues"]:
		print("BLOCKED -- resolve every issue (create pending Items first) before prices/stock can be applied.")
		return {"applied": False, "blocked": True, "stats": plan["stats"]}
	if not apply:
		print("DRY_RUN -- no prices or stock posted.")
		return {"applied": False, "blocked": False, "stats": plan["stats"]}
	if PRICE_SCOPE not in ("stock_only", "all_matched"):
		frappe.throw("PRICE_SCOPE is not decided; refusing to apply")

	_assert_opening_state(plan)
	price_changes = apply_prices(plan["prices"])
	pos = _submit_chunks(plan["positive"], positive=True)
	neg = _submit_chunks(plan["negative"], positive=False)
	result = {
		"price_changes": price_changes,
		"positive_entries": pos,
		"negative_entries": neg,
		"price_check": verify_prices(plan["prices"]),
		"stock_check": verify_stock(plan),
	}
	print(result)
	return result
