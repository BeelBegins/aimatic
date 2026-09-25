"""S5 Sector C selling prices and opening stock (mock first).

Source: 1005stockposition.xls
- Sale Price -> S5 - Sector C Selling Price List
- Quantity + tax-exclusive Cost Price -> S5 - Sector C - SSM

The source ``Cost Price`` is the same legacy CurCost concept documented in
``import.md``: reverse-calculate it with the Item's FBR tax rate before using
it as stock valuation. Negative Quantity is posted as Material Issue.

Hard-targets live ``szl``. Dry-run by default. Apply only after a verified
``szl`` backup. Crystal header is ignored; columns are positional.

Run from ``bench --site szl console`` using one explicit namespace (IPython
otherwise gives nested exec() a split globals/locals namespace):

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s5_prices_and_stock.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)  # live apply, only after backup + dry-run
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict
from pathlib import Path

import frappe
import xlrd

TARGET_SITE = "szl"
SOURCE_XLS = "/home/nabeel/frappe-bench/sites/szl/private/files/1005stockposition.xls"
SOURCE_SHA256 = "7f5ec5fb475771045188d30facb5991a09b484600c6f062ffdd5b4558ac04ec8"
EXPECTED_SOURCE_ROWS = 6392

COMPANY = "Siezal Supermarket"
BRANCH = "S5 - Sector C"
WAREHOUSE = "S5 - Sector C - SSM"
COST_CENTER = "S5 - Sector C - SSM"
PRICE_LIST = "S5 - Sector C Selling Price List"
UOM = "Pcs"
REQUIRED_CURRENCY = "PKR"
POSTING_DATE = "2026-09-20"

STOCK_ENTRY_CHUNK_SIZE = 200
PRICE_COMMIT_EVERY = 250
POSITIVE_TAG = "S5-ONHAND-IMPORT-2026-09-20"
NEGATIVE_TAG = "S5-ONHAND-NEGATIVE-IMPORT-2026-09-20"

# Explicitly excluded during the approved Item-create phase: four TESTING
# rows and nine rows whose master SubCatName was blank/literal "NULL".
# Description is checked too so a same-looking barcode in a different source
# file cannot be silently skipped.
APPROVED_EXCLUSIONS = {
	"14": "TEST3",
	"DC70": "DELIVERY CHARGES",
	"8961100096855": "UJAB BIN ROLL NO-40",
	"12": "TEST1",
	"6261422503119": "RANI MANGO BOTTLE 200ML",
	# Short codes 1 / 001 / 0001 collapse onto Item "Test".
	"1": "TEST",
	"001": "TOOL SET 001",
	"0001": "PLAY MAT",
}

APPROVED_SALE_PRICES = {
	"STO-ITEM-2026-10247": 229,
	"STO-ITEM-2026-01210": 669,
	"STO-ITEM-2026-16573": 1879,
	"STO-ITEM-2026-10318": 1235,
	"STO-ITEM-2026-07622": 279,
	"STO-ITEM-2026-10903": 2865,
	"STO-ITEM-2026-00406": 285,
	"STO-ITEM-2026-13825": 2359,
	"STO-ITEM-2026-08024": 20,
	"STO-ITEM-2026-12451": 1129,
	"STO-ITEM-2026-05547": 85,
	"STO-ITEM-2026-10440": 79,
	"STO-ITEM-2026-09541": 260,
	"STO-ITEM-2026-10902": 1920,
	"STO-ITEM-2026-11934": 1319,
	"STO-ITEM-2026-00407": 285,
	"STO-ITEM-2026-11933": 525,
	"STO-ITEM-2026-00091": 175,
	"STO-ITEM-2026-07981": 150,
	"STO-ITEM-2026-07635": 550,
}

APPROVED_ZERO_NET_STOCK_ITEMS = {
	"STO-ITEM-2026-11136",  # Candyland Sour Bites +24/-24, residual -0.24
	"STO-ITEM-2026-00406",  # Shan Nihari +16/-16, residual 252.32
	"STO-ITEM-2026-13825",  # Canbebe Jumbo +3/-3, residual -1078.80
}


def _assert_site_and_source():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing S5 price/stock import: expected site {TARGET_SITE}, got {site}")
	path = Path(SOURCE_XLS)
	if not path.is_file():
		frappe.throw(f"S5 source missing: {SOURCE_XLS}")
	digest = hashlib.sha256(path.read_bytes()).hexdigest()
	if digest != SOURCE_SHA256:
		frappe.throw(f"S5 source hash changed: expected {SOURCE_SHA256}, got {digest}")


def _norm_code(value) -> str:
	if value is None:
		return ""
	text = str(value).strip()
	if text.endswith(".0") and text.replace(".", "", 1).isdigit():
		text = text[:-2]
	return text


def _variants(code: str) -> set[str]:
	out = {code, code.upper(), code.lower()}
	if code.isdigit():
		stripped = code.lstrip("0") or "0"
		out.add(stripped)
		for width in (12, 13, 14):
			out.add(code.zfill(width))
			out.add(stripped.zfill(width))
	return {value for value in out if value}


def _as_float(value) -> float:
	try:
		return float(str(value or "").strip() or 0)
	except (TypeError, ValueError):
		return 0.0


def _source_text(value) -> str:
	if isinstance(value, float) and value == int(value):
		return str(int(value))
	return str(value or "").strip()


def parse_s5_rows() -> list[dict]:
	workbook = xlrd.open_workbook(SOURCE_XLS)
	sheet = workbook.sheet_by_index(0)
	# Crystal Reports garbles this export's header row. Parse the seven
	# columns positionally like the barcode audit: Barcode1, Description,
	# Quantity, Cost Price, Sale Price, Barcode2, Total.

	rows = []
	for row_number in range(1, sheet.nrows):
		values = sheet.row_values(row_number)
		if not values or not any(str(cell).strip() for cell in values):
			continue
		while len(values) < 7:
			values.append("")
		barcode1 = _norm_code(_source_text(values[0]))
		barcode2 = _norm_code(_source_text(values[5]))
		if not barcode1 and not barcode2:
			continue
		rows.append(
			{
				"row": row_number + 1,
				"Barcode1": barcode1,
				"Barcode2": barcode2,
				"Description": _source_text(values[1]),
				"Onhand": _as_float(values[2]),
				"CurCost": _as_float(values[3]),
				"SalePrice": _as_float(values[4]),
			}
		)
	if len(rows) != EXPECTED_SOURCE_ROWS:
		frappe.throw(f"Unexpected S5 source row count: expected {EXPECTED_SOURCE_ROWS}, got {len(rows)}")
	return rows


def _assert_accounting_context():
	warehouse = frappe.db.get_value(
		"Warehouse", WAREHOUSE, ["company", "custom_branch"], as_dict=True
	)
	if not warehouse:
		frappe.throw(f"Missing warehouse {WAREHOUSE}")
	if warehouse.company != COMPANY or warehouse.custom_branch != BRANCH:
		frappe.throw(
			f"Unexpected warehouse context: company={warehouse.company}, branch={warehouse.custom_branch}"
		)
	branch_cost_center = frappe.db.get_value("Branch", BRANCH, "cost_center")
	if branch_cost_center != COST_CENTER:
		frappe.throw(f"Unexpected S5 cost center: expected {COST_CENTER}, got {branch_cost_center}")

	price_list = frappe.db.get_value(
		"Price List", PRICE_LIST, ["enabled", "selling", "currency"], as_dict=True
	)
	if not price_list or not price_list.enabled or not price_list.selling:
		frappe.throw(f"Missing or disabled Selling Price List {PRICE_LIST}")
	if price_list.currency != REQUIRED_CURRENCY:
		frappe.throw(f"Unexpected {PRICE_LIST} currency: {price_list.currency}")
	if frappe.db.get_single_value("Stock Settings", "valuation_method") != "Moving Average":
		frappe.throw("Stock Settings valuation_method must be Moving Average")
	if int(frappe.db.get_value("UOM", UOM, "must_be_whole_number") or 0):
		frappe.throw(f"UOM {UOM} must allow fractional quantities")


def barcode_indexes():
	barcode_exact = defaultdict(set)
	item_exact = defaultdict(set)
	variant = defaultdict(set)
	for barcode, parent in frappe.db.sql(
		"SELECT barcode, parent FROM `tabItem Barcode` WHERE IFNULL(barcode, '') != ''"
	):
		code = str(barcode).strip()
		if not code:
			continue
		barcode_exact[code.lower()].add(parent)
		for candidate in _variants(code):
			variant[candidate.lower()].add(parent)
	# Some legacy catalog Items are literally named after their barcode and
	# have no Item Barcode child row (a gap that S7 only found after cutover).
	for item_code in frappe.get_all("Item", pluck="name"):
		code = str(item_code).strip()
		item_exact[code.lower()].add(item_code)
		for candidate in _variants(code):
			variant[candidate.lower()].add(item_code)
	return barcode_exact, item_exact, variant


def _resolve_code(code: str, barcode_exact, item_exact, variant) -> set[str]:
	if not code:
		return set()
	# Prefer an actual Item Barcode owner. Fall back to Item.name only for
	# older barcode-named Items that have no barcode child row.
	direct_barcode = barcode_exact.get(code.lower(), set())
	if direct_barcode:
		return set(direct_barcode)
	direct_item = item_exact.get(code.lower(), set())
	if direct_item:
		return set(direct_item)
	matches = set()
	for candidate in _variants(code):
		matches.update(variant.get(candidate.lower(), set()))
	return matches


def resolve_item(row, barcode_exact, item_exact, variant):
	"""Barcode1 owns the row; Barcode2 is fallback only.

	S5's six reviewed Barcode1/Barcode2 collisions are genuinely different
	products. In all six, the source Description matches Barcode1's Item, so
	combining both barcode match sets (the old S7 behavior) would wrongly skip
	the row as ambiguous.
	"""
	primary = _resolve_code(row["Barcode1"], barcode_exact, item_exact, variant)
	secondary = _resolve_code(row["Barcode2"], barcode_exact, item_exact, variant)
	if len(primary) == 1:
		return next(iter(primary)), "barcode1", secondary
	if len(primary) > 1:
		return None, "ambiguous_barcode1", primary
	if len(secondary) == 1:
		return next(iter(secondary)), "barcode2_fallback", secondary
	if len(secondary) > 1:
		return None, "ambiguous_barcode2", secondary
	return None, "unmatched", set()


def exclusive_rate(inclusive_rate: float, tax_rate: float) -> float:
	if inclusive_rate <= 0 or tax_rate <= 0:
		return inclusive_rate
	sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
	return round(inclusive_rate - sales_tax, 2)


def get_item_tax(item_code: str, cache: dict):
	if item_code in cache:
		return cache[item_code]
	category = frappe.db.get_value("Item", item_code, "custom_fbr_tax_category")
	if not category or not frappe.db.exists("FBR Tax Category", category):
		cache[item_code] = None
		return None
	cache[item_code] = {
		"category": category,
		"tax_rate": _as_float(frappe.db.get_value("FBR Tax Category", category, "tax_rate")),
	}
	return cache[item_code]


def _is_approved_exclusion(row) -> bool:
	return (
		row["Barcode1"] in APPROVED_EXCLUSIONS
		and row["Description"].strip().upper() == APPROVED_EXCLUSIONS[row["Barcode1"]]
	)


def build_plan(rows):
	barcode_exact, item_exact, variant = barcode_indexes()
	tax_cache = {}
	disabled = set(frappe.get_all("Item", filters={"disabled": 1}, pluck="name"))
	stats = Counter()
	issues = []
	warnings = []
	by_item = {}

	for row in rows:
		stats["source_rows"] += 1
		if _is_approved_exclusion(row):
			stats["approved_excluded"] += 1
			continue
		item_code, resolution, other_matches = resolve_item(row, barcode_exact, item_exact, variant)
		if not item_code:
			stats[resolution] += 1
			issues.append(
				{
					"row": row["row"],
					"barcode1": row["Barcode1"],
					"barcode2": row["Barcode2"],
					"description": row["Description"][:80],
					"reason": resolution,
					"items": sorted(other_matches),
				}
			)
			continue
		stats[resolution] += 1
		if resolution == "barcode1" and other_matches and item_code not in other_matches:
			stats["barcode2_conflict_ignored"] += 1
			warnings.append(
				{
					"row": row["row"],
					"barcode1_item": item_code,
					"barcode2_items": sorted(other_matches),
					"description": row["Description"][:80],
				}
			)
		if item_code in disabled:
			stats["disabled"] += 1
			issues.append({"row": row["row"], "reason": "disabled", "items": [item_code]})
			continue
		tax = get_item_tax(item_code, tax_cache)
		if tax is None:
			stats["missing_fbr_category"] += 1
			issues.append({"row": row["row"], "reason": "missing_fbr_category", "items": [item_code]})
			continue

		rate = exclusive_rate(row["CurCost"], tax["tax_rate"])
		rec = by_item.setdefault(
			item_code,
			{
				"item_code": item_code,
				"onhand": 0.0,
				"exclusive_value": 0.0,
				"inclusive_value": 0.0,
				"sale_prices": set(),
				"rows": [],
			},
		)
		rec["onhand"] += row["Onhand"]
		rec["exclusive_value"] += row["Onhand"] * rate
		rec["inclusive_value"] += row["Onhand"] * row["CurCost"]
		if row["SalePrice"] > 0:
			rec["sale_prices"].add(round(row["SalePrice"], 2))
		rec["rows"].append(row["row"])

	prices = []
	positive = []
	negative = []
	for item_code, rec in by_item.items():
		if len(rec["rows"]) > 1:
			stats["merged_duplicate_item_rows"] += len(rec["rows"]) - 1
		if len(rec["sale_prices"]) > 1:
			selected_price = APPROVED_SALE_PRICES.get(item_code)
			if selected_price not in rec["sale_prices"]:
				stats["conflicting_sale_prices"] += 1
				issues.append(
					{
						"reason": "conflicting_sale_prices",
						"items": [item_code],
						"rows": rec["rows"],
						"prices": sorted(rec["sale_prices"]),
						"approved_price": selected_price,
					}
				)
				continue
			stats["approved_sale_price_choice"] += 1
			prices.append({"item_code": item_code, "sale_price": selected_price})
		elif rec["sale_prices"]:
			prices.append({"item_code": item_code, "sale_price": next(iter(rec["sale_prices"]))})
		else:
			stats["zero_sale_price"] += 1

		qty = round(rec["onhand"], 6)
		if qty:
			rate = round(rec["exclusive_value"] / qty, 6)
			stock_row = {
				"item_code": item_code,
				"qty": abs(qty),
				"signed_qty": qty,
				"rate": rate,
				"expected_value": round(rec["exclusive_value"], 6),
				"expected_inclusive_value": round(rec["inclusive_value"], 6),
			}
			if qty > 0:
				positive.append(stock_row)
			else:
				negative.append(stock_row)
		else:
			stats["zero_onhand"] += 1
			if abs(rec["exclusive_value"]) > 0.01:
				if item_code in APPROVED_ZERO_NET_STOCK_ITEMS:
					stats["approved_zero_net_residual_skipped"] += 1
					warnings.append(
						{
							"reason": "approved_zero_net_residual_skipped",
							"item": item_code,
							"rows": rec["rows"],
							"value": round(rec["exclusive_value"], 2),
						}
					)
				else:
					stats["zero_net_qty_nonzero_value"] += 1
					issues.append(
						{
							"reason": "zero_net_qty_nonzero_value",
							"items": [item_code],
							"rows": rec["rows"],
							"value": round(rec["exclusive_value"], 2),
						}
					)

	stats["resolved_items"] = len(by_item)
	stats["price_rows"] = len(prices)
	stats["positive_stock"] = len(positive)
	stats["negative_stock"] = len(negative)
	stats["blocking_issues"] = len(issues)
	return {
		"stats": stats,
		"issues": issues,
		"warnings": warnings,
		"prices": prices,
		"positive": positive,
		"negative": negative,
		"source_inclusive_signed_stock_value": round(sum(r["inclusive_value"] for r in by_item.values()), 2),
		"source_exclusive_signed_stock_value": round(sum(r["exclusive_value"] for r in by_item.values()), 2),
		"inclusive_signed_stock_value": round(sum(r["expected_inclusive_value"] for r in positive + negative), 2),
		"exclusive_signed_stock_value": round(sum(r["expected_value"] for r in positive + negative), 2),
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
	duplicates = {item: values for item, values in by_item.items() if len(values) > 1}
	if duplicates:
		frappe.throw(f"Duplicate Item Prices already exist in {PRICE_LIST}: {list(duplicates)[:10]}")
	return {item: values[0] for item, values in by_item.items()}


def apply_prices(prices):
	existing = _existing_prices()
	changed = 0
	for rec in prices:
		item_code = rec["item_code"]
		rate = rec["sale_price"]
		current = existing.get(item_code)
		if current and abs(float(current.price_list_rate or 0) - rate) < 0.005:
			continue
		if current:
			frappe.db.set_value("Item Price", current.name, "price_list_rate", rate)
		else:
			price = frappe.new_doc("Item Price")
			price.item_code = item_code
			price.price_list = PRICE_LIST
			price.price_list_rate = rate
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
	return [f"{base} chunk {start // STOCK_ENTRY_CHUNK_SIZE}" for start in range(0, len(rows), STOCK_ENTRY_CHUNK_SIZE)]


def _assert_opening_state(plan):
	expected_tags = set(_expected_chunk_tags(plan["positive"], positive=True)) | set(
		_expected_chunk_tags(plan["negative"], positive=False)
	)
	existing = frappe.db.sql(
		"SELECT name, remarks, docstatus FROM `tabStock Entry` WHERE remarks LIKE 'S5-ONHAND-%'",
		as_dict=True,
	)
	unexpected = [row for row in existing if row.remarks not in expected_tags]
	if unexpected:
		frappe.throw(f"Unexpected S5 opening tags already exist: {unexpected[:10]}")
	duplicate_tags = Counter(row.remarks for row in existing if row.docstatus == 1)
	duplicate_tags = {tag: count for tag, count in duplicate_tags.items() if count > 1}
	if duplicate_tags:
		frappe.throw(f"Duplicate submitted S5 opening tags: {duplicate_tags}")
	nonzero_bins = frappe.db.sql(
		"SELECT COUNT(*) FROM tabBin WHERE warehouse=%s AND actual_qty != 0", WAREHOUSE
	)[0][0]
	if nonzero_bins and not any(row.docstatus == 1 for row in existing):
		frappe.throw(f"{WAREHOUSE} already has {nonzero_bins} nonzero bins without S5 opening tags")


def _submit_chunks(rows, *, positive: bool):
	if not rows:
		return {"created": 0, "skipped": 0}
	account = _temporary_opening_account()
	created = 0
	skipped = 0
	for start in range(0, len(rows), STOCK_ENTRY_CHUNK_SIZE):
		chunk = rows[start : start + STOCK_ENTRY_CHUNK_SIZE]
		tag = _expected_chunk_tags(rows, positive=positive)[start // STOCK_ENTRY_CHUNK_SIZE]
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
				"expense_account": account,
				"cost_center": COST_CENTER,
				"branch": BRANCH,
			}
			if positive:
				line["t_warehouse"] = WAREHOUSE
				line["valuation_rate"] = stock_row["rate"]
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
			frappe.throw(
				f"{entry.name} missing S5 dimensions: detail={blank_detail}, gl={blank_gl}"
			)
		frappe.db.commit()
		created += 1
		print(f"Posted {entry.name} {tag} rows={len(chunk)}")
	return {"created": created, "skipped": skipped}


def verify_prices(prices):
	existing = _existing_prices()
	missing = []
	mismatched = []
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
	expected = {row["item_code"]: row["signed_qty"] for row in plan["positive"] + plan["negative"]}
	missing = []
	mismatched = []
	for item_code, qty in expected.items():
		actual = float(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": WAREHOUSE}, "actual_qty") or 0
		)
		if abs(actual - qty) > 0.0001:
			(missing if actual == 0 else mismatched).append((item_code, qty, actual))
	bin_summary = frappe.db.sql(
		"""SELECT COUNT(*), ROUND(SUM(stock_value), 2)
		FROM tabBin WHERE warehouse=%s AND actual_qty != 0""",
		WAREHOUSE,
	)[0]
	tag_pattern = "S5-ONHAND-%"
	sle_count = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabStock Entry` se ON se.name=sle.voucher_no
		WHERE sle.voucher_type='Stock Entry' AND se.remarks LIKE %s AND sle.is_cancelled=0""",
		tag_pattern,
	)[0][0]
	blank_detail = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name=sed.parent
		WHERE se.docstatus=1 AND se.remarks LIKE %s
		  AND (IFNULL(sed.cost_center, '')='' OR IFNULL(sed.branch, '')='')""",
		tag_pattern,
	)[0][0]
	blank_gl = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabGL Entry` gl
		INNER JOIN `tabStock Entry` se ON se.name=gl.voucher_no
		WHERE gl.voucher_type='Stock Entry' AND se.docstatus=1 AND se.remarks LIKE %s
		  AND (IFNULL(gl.cost_center, '')='' OR IFNULL(gl.branch, '')='')""",
		tag_pattern,
	)[0][0]
	return {
		"expected_rows": len(expected),
		"nonzero_bins": int(bin_summary[0] or 0),
		"bin_stock_value": float(bin_summary[1] or 0),
		"expected_stock_value": plan["exclusive_signed_stock_value"],
		"stock_value_delta": round(float(bin_summary[1] or 0) - plan["exclusive_signed_stock_value"], 2),
		"sle_count": sle_count,
		"missing_or_zero_bin": len(missing),
		"qty_mismatch": len(mismatched),
		"blank_detail_dimensions": blank_detail,
		"blank_gl_dimensions": blank_gl,
		"mismatch_sample": mismatched[:10] + missing[:10],
	}


def main(apply: bool = False):
	_assert_site_and_source()
	_assert_accounting_context()
	rows = parse_s5_rows()
	plan = build_plan(rows)
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"source_sha256": SOURCE_SHA256,
			"warehouse": WAREHOUSE,
			"price_list": PRICE_LIST,
			"posting_date": POSTING_DATE,
			"stats": dict(plan["stats"]),
			"source_inclusive_signed_stock_value": plan["source_inclusive_signed_stock_value"],
			"source_exclusive_signed_stock_value": plan["source_exclusive_signed_stock_value"],
			"inclusive_signed_stock_value": plan["inclusive_signed_stock_value"],
			"exclusive_signed_stock_value": plan["exclusive_signed_stock_value"],
			"issue_counts": dict(Counter(issue["reason"] for issue in plan["issues"])),
			"issue_sample": plan["issues"][:20],
			"warning_sample": plan["warnings"][:10],
		}
	)
	if plan["issues"]:
		print("BLOCKED — resolve every issue before prices or stock can be applied.")
		return {"applied": False, "blocked": True, "stats": plan["stats"]}
	if not apply:
		print("DRY_RUN — no prices or stock posted.")
		return {"applied": False, "blocked": False, "stats": plan["stats"]}

	_assert_opening_state(plan)
	price_changes = apply_prices(plan["prices"])
	positive_entries = _submit_chunks(plan["positive"], positive=True)
	negative_entries = _submit_chunks(plan["negative"], positive=False)
	price_check = verify_prices(plan["prices"])
	stock_check = verify_stock(plan)
	result = {
		"price_changes": price_changes,
		"positive_entries": positive_entries,
		"negative_entries": negative_entries,
		"price_check": price_check,
		"stock_check": stock_check,
	}
	print(result)
	return result
