"""S2 wrong-list rows the user cleared one by one (2026-10-11).

Follow-up to import_s2_hold_rows.py. Each entry below is an explicit user
decision, not a similarity guess: the file row, the szl Item that receives its
stock, and any unit conversion. Barcodes are never moved off other Items.

 1 STUNT CAR 663 -23          -> STO-ITEM-2026-23831 (exact name)
 2 TOY DOLL SET 209-1         -> STO-ITEM-2026-02406 (exact name)
 3 HELLO KITTY NAIL REMOVER   -> STO-ITEM-2026-00919 (exact name)
 4 HILALL AAMRUS 50PCS        -> STO-ITEM-2026-08042 (same barcode)
 5 HILALL AAMRUS JAR          -> STO-ITEM-2026-08042: 53 pieces = 1.06 boxes of 50
 6 HILALL HAJMOLA 50PCS       -> STO-ITEM-2026-17399 (same barcode)
 7 HAIR ACCESSORIES code 80   -> STO-ITEM-2026-27332 (MRP 80)
 8 AL-H KARACHI MIX NIMKO     -> STO-ITEM-2026-02269 (exact name)
 9 BIRTHDAY ITEM SASH CHINA   -> STO-ITEM-2026-00013
 S SHAHTAJ pouch 384 + carton -34 x 5 = -170 pouch -> STO-ITEM-2026-12632
   (the carton row takes the pouch cost 498.48 per piece)

Stock is posted as Material Receipt; GST backed out of the tax-inclusive cost is
booked as a separate Opening Entry (same as the S2 main/hold passes). A selling
price is only created where the S2 Selling Price List has none (existing prices
are kept). Dry-run by default; apply needs a verified backup.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_hold_rows_cleared.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run
    ns["main"](apply=True)
"""

from __future__ import annotations

from collections import defaultdict

import frappe

STOCK_SCRIPT = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_prices_and_stock.py"
POSITIVE_TAG = "S2-ONHAND-HOLD2-IMPORT-2026-10-11"
NEGATIVE_TAG = "S2-ONHAND-HOLD2-NEGATIVE-IMPORT-2026-10-11"
GST_TAG = "S2-ONHAND-HOLD2-GST-OPENING-2026-10-11"

# file row -> (expected ItemCode in the file, target szl Item, qty multiplier, cost multiplier)
# cost_override: take the incl cost per szl unit from another file row instead.
PLAN = [
	{"n": 1, "row": 17118, "code": "663", "item": "STO-ITEM-2026-23831", "qty_mult": 1, "cost_mult": 1},
	{"n": 2, "row": 5238, "code": "111244", "item": "STO-ITEM-2026-02406", "qty_mult": 1, "cost_mult": 1},
	{"n": 3, "row": 19486, "code": "6971260895016", "item": "STO-ITEM-2026-00919", "qty_mult": 1, "cost_mult": 1},
	{"n": 4, "row": 698, "code": "0769503786865", "item": "STO-ITEM-2026-08042", "qty_mult": 1, "cost_mult": 1},
	# 53 pieces of a 50-piece box: qty in boxes, cost per box = cost per piece * 50
	{"n": 5, "row": 38702, "code": "AG5", "item": "STO-ITEM-2026-08042", "qty_mult": 1 / 50, "cost_mult": 50, "no_price": True},
	{"n": 6, "row": 699, "code": "0769503786872", "item": "STO-ITEM-2026-17399", "qty_mult": 1, "cost_mult": 1},
	{"n": 7, "row": 22820, "code": "80", "item": "STO-ITEM-2026-27332", "qty_mult": 1, "cost_mult": 1},
	{"n": 8, "row": 8402, "code": "1947107218", "item": "STO-ITEM-2026-02269", "qty_mult": 1, "cost_mult": 1},
	{"n": 9, "row": 39, "code": "00000000115", "item": "STO-ITEM-2026-00013", "qty_mult": 1, "cost_mult": 1},
	{"n": "S1", "row": 31007, "code": "8964000993033", "item": "STO-ITEM-2026-12632", "qty_mult": 1, "cost_mult": 1},
	# carton row: -34 cartons x 5 pouches; cost per pouch = the pouch row's cost
	{"n": "S2", "row": 31005, "code": "8964000993019", "item": "STO-ITEM-2026-12632", "qty_mult": 5, "cost_from_row": 31007, "no_price": True},
]


def _load():
	ns = {}
	exec(compile(open(STOCK_SCRIPT).read(), STOCK_SCRIPT, "exec"), ns)
	return ns


def make_plan(ns):
	rows = {r["row"]: r for r in ns["parse_rows"]()}
	cache = {}
	issues, per_item = [], defaultdict(lambda: {"qty": 0.0, "excl": 0.0, "incl": 0.0, "sale": 0.0, "src": []})
	for p in PLAN:
		r = rows.get(p["row"])
		if not r or r["Barcode1"] != p["code"]:
			issues.append({"n": p["n"], "reason": "file_row_mismatch", "row": p["row"], "found": r and r["Barcode1"]})
			continue
		item = p["item"]
		if not frappe.db.exists("Item", item) or frappe.db.get_value("Item", item, "disabled"):
			issues.append({"n": p["n"], "reason": "item_missing_or_disabled", "item": item})
			continue
		cat = frappe.db.get_value("Item", item, "custom_fbr_tax_category")
		rate = ns["_tax_rate_for_category"](cat, cache)
		if rate is None:
			issues.append({"n": p["n"], "reason": "missing_fbr_category", "item": item})
			continue
		qty = r["Onhand"] * p["qty_mult"]
		cost = rows[p["cost_from_row"]]["CurCost"] if "cost_from_row" in p else r["CurCost"] * p.get("cost_mult", 1)
		excl_rate = ns["exclusive_rate"](cost, rate)
		rec = per_item[item]
		rec["qty"] += qty
		rec["excl"] += qty * excl_rate
		rec["incl"] += qty * cost
		if not p.get("no_price"):
			rec["sale"] = max(rec["sale"], r["SalePrice"])
		rec["src"].append((p["n"], p["row"], r["Description"], round(qty, 6), round(cost, 4)))
	existing_prices = {
		r[0]: float(r[1])
		for r in frappe.db.sql("select item_code, price_list_rate from `tabItem Price` where price_list=%s", ns["PRICE_LIST"])
	}
	existing_bins = {
		r[0]: (float(r[1]), float(r[2] or 0))
		for r in frappe.db.sql(
			"select item_code, actual_qty, valuation_rate from tabBin where warehouse=%s", ns["WAREHOUSE"]
		)
	}
	positive, negative, prices, kept = [], [], [], []
	for item, rec in per_item.items():
		qty = round(rec["qty"], 6)
		if qty:
			row = {
				"item_code": item,
				"qty": abs(qty),
				"signed_qty": qty,
				"rate": round(rec["excl"] / qty, 6),
				"expected_value": round(rec["excl"], 6),
				"expected_inclusive_value": round(rec["incl"], 6),
			}
			(positive if qty > 0 else negative).append(row)
		if rec["sale"] > 0:
			if item in existing_prices:
				kept.append((item, existing_prices[item], rec["sale"]))
			else:
				prices.append({"item_code": item, "sale_price": round(rec["sale"], 2)})
	return {
		"per_item": per_item,
		"positive": positive,
		"negative": negative,
		"prices": prices,
		"kept_prices": kept,
		"issues": issues,
		"existing_bins": {i: existing_bins.get(i) for i in per_item},
	}


def _create_prices(ns, prices):
	"""Create a Pcs price only where this Item has none on the S2 list.

	Not ns["apply_prices"]: its "one price per Item" guard now trips on other
	users' legitimate Box-UOM prices on the same list (e.g. Pepsi 2.25LTR), so
	this checks only the Items it is about to price."""
	created = 0
	for rec in prices:
		if frappe.db.exists(
			"Item Price", {"price_list": ns["PRICE_LIST"], "item_code": rec["item_code"], "uom": ns["UOM"]}
		):
			continue
		price = frappe.new_doc("Item Price")
		price.item_code = rec["item_code"]
		price.price_list = ns["PRICE_LIST"]
		price.price_list_rate = rec["sale_price"]
		price.uom = ns["UOM"]
		price.currency = ns["REQUIRED_CURRENCY"]
		price.selling = 1
		price.insert(ignore_permissions=True)
		created += 1
	frappe.db.commit()
	return created


def _post_gst(ns, total_gst):
	if frappe.db.exists("Journal Entry", {"cheque_no": GST_TAG, "docstatus": ["!=", 2]}):
		return "already_posted"
	if not total_gst:
		return "zero"
	company, branch, cost_center = ns["COMPANY"], ns["BRANCH"], ns["COST_CENTER"]
	temp = frappe.db.get_value("Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0})
	date = ns["POSTING_DATE"]
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.company = company
	je.posting_date = date
	je.is_opening = "Yes"
	je.cheque_no = GST_TAG
	je.cheque_date = date
	je.user_remark = "S2 cleared wrong-list rows: GST portion backed out of legacy CurCost (see import.md)"
	amount, debit = abs(total_gst), total_gst > 0
	common = {"branch": branch, "cost_center": cost_center}
	je.append("accounts", {"account": "GST - SSM", ("debit_in_account_currency" if debit else "credit_in_account_currency"): amount, **common})
	je.append("accounts", {"account": temp, ("credit_in_account_currency" if debit else "debit_in_account_currency"): amount, **common})
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	return je.name


def main(apply: bool = False):
	ns = _load()
	ns["_assert_site_and_source"]()
	ns["_assert_accounting_context"]()
	plan = make_plan(ns)
	rows = plan["positive"] + plan["negative"]
	incl = round(sum(r["expected_inclusive_value"] for r in rows), 2)
	excl = round(sum(r["expected_value"] for r in rows), 2)
	print(
		{
			"apply": apply,
			"items": len(rows),
			"incl": incl,
			"excl": excl,
			"gst": round(incl - excl, 2),
			"prices_to_create": plan["prices"],
			"existing_prices_kept": plan["kept_prices"],
			"existing_s2_bins": plan["existing_bins"],
			"issues": plan["issues"],
		}
	)
	for item, rec in plan["per_item"].items():
		print(" ", item, "qty", round(rec["qty"], 4), "incl", round(rec["incl"], 2), "excl", round(rec["excl"], 2), rec["src"])
	if not apply:
		print("DRY_RUN -- nothing posted.")
		return plan
	if plan["issues"]:
		frappe.throw(f"{len(plan['issues'])} blocking issues")
	ns["POSITIVE_TAG"], ns["NEGATIVE_TAG"] = POSITIVE_TAG, NEGATIVE_TAG
	price_changes = _create_prices(ns, plan["prices"])
	pos = ns["_submit_chunks"](plan["positive"], positive=True)
	neg = ns["_submit_chunks"](plan["negative"], positive=False)
	gst_je = _post_gst(ns, round(incl - excl, 2))
	result = {"prices": price_changes, "positive": pos, "negative": neg, "gst_je": gst_je}
	print(result)
	return result
