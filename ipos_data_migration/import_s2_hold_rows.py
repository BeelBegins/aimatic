"""S2 HOLD rows: the barcode-conflict and double-hit rows held out of the main pass.

Decisions (user, 2026-10-11, after reviewing s2_hold_review.xlsx):
- Take the file rows (they are "almost the same item"): post them onto an
  EXISTING szl Item. Never create a new Item here.
- Add the file's barcode to that existing Item when it is not on any Item yet.
- ItemCode 22 (LASTIC LATGE BLACK) is skipped.
- Rows that are plainly a different product from every candidate Item are NOT
  posted: they go to a separate "wrong" list with their cost value.

Target Item per file row = the candidate (Items owning ItemCode/RefCode) whose
name is most similar to the file Description, if similarity >= SIMILARITY_MIN.
When the ItemCode owner is within TIE_MARGIN of the best, the ItemCode owner
wins (same "Barcode1 owns the row" rule as S6). Several rows landing on one
Item merge: qty summed, valuation = weighted tax-exclusive cost, selling price =
the higher Slprice (S4 sign-off 2026-09-13, reused by S6). Same tax-inclusive ->
exclusive handling and Temporary Opening posting as import_s2_prices_and_stock.py.

Dry-run by default; apply needs a verified backup and explicit approval.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_hold_rows.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()            # dry-run + review workbook
    ns["main"](apply=True)  # live
"""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict

import frappe

TARGET_SITE = "szl"
STOCK_SCRIPT = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/import_s2_prices_and_stock.py"
SKIP_ITEMCODES = {"22"}  # LASTIC LATGE BLACK (user: skip)
# User-approved 2026-10-11 after reviewing the cost-mismatch merges: both rows have
# nonzero qty at very different costs, so merging would distort valuation. Their rows
# go to the wrong/not-posted list instead.
EXCLUDE_ITEMS = {"STO-ITEM-2026-08042", "STO-ITEM-2026-02406", "STO-ITEM-2026-12632"}
SIMILARITY_MIN = 0.6
TIE_MARGIN = 0.1
COST_DIFF_FLAG = 0.25
POSITIVE_TAG = "S2-ONHAND-HOLD-IMPORT-2026-10-11"
NEGATIVE_TAG = "S2-ONHAND-HOLD-NEGATIVE-IMPORT-2026-10-11"
GST_TAG = "S2-ONHAND-HOLD-GST-OPENING-2026-10-11"
REVIEW_XLSX = "/tmp/claude-1000/-home-nabeel-frappe-bench/2e3d3ba7-3929-4fa2-b2bf-9138d2f6d34b/scratchpad/s2_hold_plan.xlsx"


def _load():
	ns = {}
	exec(compile(open(STOCK_SCRIPT).read(), STOCK_SCRIPT, "exec"), ns)
	return ns


def _tokens(text: str) -> set[str]:
	return {t for t in re.split(r"[^A-Z0-9]+", (text or "").upper()) if t}


def similarity(a: str, b: str) -> float:
	ta, tb = _tokens(a), _tokens(b)
	seq = difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
	seq2 = difflib.SequenceMatcher(None, (a or "").upper(), (b or "").upper()).ratio()
	overlap = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
	return max(seq, seq2, overlap)


def _choose(row, p1, p2, names):
	"""Best candidate Item for a file row, or (None, reason)."""
	cands = sorted(p1 | p2)
	scored = [(similarity(row["Description"], names[c]), c) for c in cands]
	scored = [s for s in scored if s[0] >= SIMILARITY_MIN]
	if not scored:
		return None, "no_candidate_name_similar"
	best_s, best = max(scored)
	owner = next(iter(p1)) if len(p1) == 1 else None
	if owner and owner != best:
		owner_s = next((s for s, c in scored if c == owner), None)
		if owner_s is not None and owner_s >= best_s - TIE_MARGIN:
			return owner, "ok_itemcode_owner"
	return best, "ok"


def build(ns):
	be, ie, vr = ns["barcode_indexes"]()
	R = ns["_resolve_code"]
	rows = ns["parse_rows"]()
	names = {n.name: n.item_name or "" for n in frappe.get_all("Item", fields=["name", "item_name"])}
	disabled = set(frappe.get_all("Item", filters={"disabled": 1}, pluck="name"))
	hit_rows = defaultdict(list)
	conflicts = []
	for r in rows:
		p1, p2 = R(r["Barcode1"], be, ie, vr), R(r["Barcode2"], be, ie, vr)
		if p1 and p2 and p1 != p2:
			conflicts.append((r, p1, p2))
		elif len(p1) > 1 or (not p1 and len(p2) > 1):
			conflicts.append((r, p1, p2))
		elif len(p1 | p2) == 1:
			hit_rows[next(iter(p1 | p2))].append((r, p1, p2))
	work = []  # (row, p1, p2, kind)
	for r, p1, p2 in conflicts:
		if r["Onhand"]:
			work.append((r, p1, p2, "conflict"))
	for item, lst in hit_rows.items():
		if len(lst) > 1 and any(x[0]["Onhand"] for x in lst):
			for r, p1, p2 in lst:
				work.append((r, p1, p2, "double_hit"))

	chosen = defaultdict(list)  # item -> rows
	wrong, skipped = [], []
	for r, p1, p2, kind in work:
		if r["Barcode1"] in SKIP_ITEMCODES:
			skipped.append({**r, "kind": kind, "reason": "user_skip"})
			continue
		item, why = _choose(r, p1, p2, names)
		if not item:
			wrong.append({**r, "kind": kind, "reason": why, "cands": sorted(p1 | p2)})
			continue
		if item in disabled:
			wrong.append({**r, "kind": kind, "reason": "item_disabled", "cands": [item]})
			continue
		if item in EXCLUDE_ITEMS:
			wrong.append({**r, "kind": kind, "reason": "cost_mismatch_merge_excluded", "cands": [item]})
			continue
		chosen[item].append({**r, "kind": kind})
	return rows, names, chosen, wrong, skipped, (be, ie, vr, R)


def make_plan(ns):
	rows, names, chosen, wrong, skipped, idx = build(ns)
	be, ie, vr, R = idx
	cache = {}
	issues, positive, negative, prices, adds = [], [], [], [], []
	kept_prices, zero_net_dropped = [], []
	flags = []
	existing_bins = {
		r[0]: float(r[1])
		for r in frappe.db.sql(
			"select item_code, actual_qty from tabBin where warehouse=%s", ns["WAREHOUSE"]
		)
	}
	existing_prices = {
		r[0]: float(r[1])
		for r in frappe.db.sql(
			"select item_code, price_list_rate from `tabItem Price` where price_list=%s", ns["PRICE_LIST"]
		)
	}
	for item, rs in chosen.items():
		cat = frappe.db.get_value("Item", item, "custom_fbr_tax_category")
		rate = ns["_tax_rate_for_category"](cat, cache)
		if rate is None:
			issues.append({"item": item, "reason": "missing_fbr_category"})
			continue
		qty = sum(x["Onhand"] for x in rs)
		incl = sum(x["Onhand"] * x["CurCost"] for x in rs)
		excl = sum(x["Onhand"] * ns["exclusive_rate"](x["CurCost"], rate) for x in rs)
		costs = [x["CurCost"] for x in rs if x["CurCost"] > 0]
		if len(costs) > 1 and (max(costs) - min(costs)) / max(costs) > COST_DIFF_FLAG:
			flags.append((item, [(x["Description"][:30], x["Onhand"], x["CurCost"]) for x in rs]))
		sale = max((x["SalePrice"] for x in rs), default=0)
		if sale > 0:
			if item in existing_prices:
				kept_prices.append({"item": item, "have": existing_prices[item], "file": round(sale, 2)})
			else:
				prices.append({"item_code": item, "sale_price": round(sale, 2)})
		qty = round(qty, 6)
		if qty:
			row = {
				"item_code": item,
				"qty": abs(qty),
				"signed_qty": qty,
				"rate": round(excl / qty, 6),
				"expected_value": round(excl, 6),
				"expected_inclusive_value": round(incl, 6),
				"rows": [x["row"] for x in rs],
			}
			(positive if qty > 0 else negative).append(row)
		elif abs(excl) > 0.01:
			# User: leave the residual value out; qty stays zero (no stock entry).
			zero_net_dropped.append({"item": item, "value_excl": round(excl, 2), "value_incl": round(incl, 2)})
		# barcodes from the used rows that no Item owns yet
		for x in rs:
			for code in (x["Barcode1"], x["Barcode2"]):
				if code and not R(code, be, ie, vr):
					adds.append({"item": item, "barcode": code})
	overlap = [i for i in chosen if i in existing_bins and existing_bins[i] != 0]
	return {
		"names": names,
		"chosen": chosen,
		"wrong": wrong,
		"skipped": skipped,
		"positive": positive,
		"negative": negative,
		"prices": prices,
		"adds": adds,
		"issues": issues,
		"flags": flags,
		"overlap_with_main_stock": overlap,
		"kept_prices": kept_prices,
		"zero_net_dropped": zero_net_dropped,
	}


def _write_review(plan):
	from openpyxl import Workbook

	wb = Workbook()
	ws = wb.active
	ws.title = "post"
	ws.append(["Item", "ItemName", "rows(file)", "FileDescs", "qty", "cost(excl)", "value(excl)", "value(incl)"])
	pos = {p["item_code"]: p for p in plan["positive"] + plan["negative"]}
	for item, rs in plan["chosen"].items():
		p = pos.get(item)
		ws.append(
			[
				item,
				plan["names"][item],
				",".join(str(x["row"]) for x in rs),
				" | ".join(x["Description"] for x in rs),
				p["signed_qty"] if p else sum(x["Onhand"] for x in rs),
				p["rate"] if p else 0,
				p["expected_value"] if p else 0,
				p["expected_inclusive_value"] if p else 0,
			]
		)
	w2 = wb.create_sheet("wrong_not_posted")
	w2.append(["kind", "file row", "ItemCode", "RefCode", "FileDescription", "Onhand", "CurCost", "value(incl)", "candidate items", "candidate names"])
	for x in plan["wrong"] + plan["skipped"]:
		cands = x.get("cands", [])
		w2.append(
			[
				x["kind"] + (" SKIPPED" if x["reason"] == "user_skip" else ""),
				x["row"],
				x["Barcode1"],
				x["Barcode2"],
				x["Description"],
				x["Onhand"],
				x["CurCost"],
				round(x["Onhand"] * x["CurCost"], 2),
				", ".join(cands),
				" | ".join(plan["names"].get(c, "") for c in cands),
			]
		)
	wb.save(REVIEW_XLSX)


def main(apply: bool = False):
	ns = _load()
	ns["_assert_site_and_source"]()
	ns["_assert_accounting_context"]()
	plan = make_plan(ns)
	wrong_val = round(sum(x["Onhand"] * x["CurCost"] for x in plan["wrong"]), 2)
	skip_val = round(sum(x["Onhand"] * x["CurCost"] for x in plan["skipped"]), 2)
	rows = plan["positive"] + plan["negative"]
	incl = round(sum(r["expected_inclusive_value"] for r in rows), 2)
	excl = round(sum(r["expected_value"] for r in rows), 2)
	_write_review(plan)
	print(
		{
			"site": TARGET_SITE,
			"apply": apply,
			"items_to_post_stock": len(rows),
			"positive": len(plan["positive"]),
			"negative": len(plan["negative"]),
			"incl_value": incl,
			"excl_value": excl,
			"gst": round(incl - excl, 2),
			"prices_planned": len(plan["prices"]),
			"barcodes_to_add": len(plan["adds"]),
			"wrong_rows_not_posted": len(plan["wrong"]),
			"wrong_value_incl": wrong_val,
			"skipped_rows": len(plan["skipped"]),
			"skipped_value_incl": skip_val,
			"items_also_in_main_stock": len(plan["overlap_with_main_stock"]),
			"existing_prices_kept": plan["kept_prices"],
			"zero_net_value_dropped": plan["zero_net_dropped"],
			"issue_counts": dict(Counter(i["reason"] for i in plan["issues"])),
			"issues": plan["issues"][:15],
			"cost_differs_gt25pct_merges": len(plan["flags"]),
			"review_xlsx": REVIEW_XLSX,
		}
	)
	if not apply:
		print("DRY_RUN -- nothing posted.")
		return plan
	if plan["issues"]:
		frappe.throw(f"{len(plan['issues'])} blocking issues; resolve them before apply")

	ns["POSITIVE_TAG"], ns["NEGATIVE_TAG"] = POSITIVE_TAG, NEGATIVE_TAG
	added = 0
	for a in plan["adds"]:
		if frappe.db.exists("Item Barcode", {"barcode": a["barcode"]}):
			continue
		doc = frappe.get_doc("Item", a["item"])
		uoms = {b.uom for b in doc.barcodes if b.uom}
		if len(uoms) > 1:
			frappe.throw(f"{a['item']} has mixed barcode UOMs; cannot attach {a['barcode']}")
		doc.append("barcodes", {"barcode": a["barcode"], "uom": next(iter(uoms)) if uoms else doc.stock_uom})
		doc.save(ignore_permissions=True)
		added += 1
	frappe.db.commit()
	price_changes = ns["apply_prices"](plan["prices"])
	pos = ns["_submit_chunks"](plan["positive"], positive=True)
	neg = ns["_submit_chunks"](plan["negative"], positive=False)
	gst_je = _post_gst(ns, round(incl - excl, 2))
	print(
		{
			"barcodes_added": added,
			"price_changes": price_changes,
			"positive": pos,
			"negative": neg,
			"gst_je": gst_je,
		}
	)
	return {"barcodes_added": added, "prices": price_changes, "positive": pos, "negative": neg, "gst_je": gst_je}


def _post_gst(ns, total_gst):
	"""Same Opening Entry as import_s2_gst_opening.py (Dr GST - SSM / Cr Temporary Opening)."""
	if frappe.db.exists("Journal Entry", {"cheque_no": GST_TAG, "docstatus": ["!=", 2]}):
		return "already_posted"
	if not total_gst:
		return "zero"
	company, branch, cost_center = ns["COMPANY"], ns["BRANCH"], ns["COST_CENTER"]
	temp = frappe.db.get_value("Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0})
	if not temp:
		frappe.throw(f"No Temporary Opening account under {company}")
	date = ns["POSTING_DATE"]
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.company = company
	je.posting_date = date
	je.is_opening = "Yes"
	je.cheque_no = GST_TAG
	je.cheque_date = date
	je.user_remark = "S2 hold-row opening stock: GST portion backed out of legacy CurCost (see import.md)"
	amount, debit = abs(total_gst), total_gst > 0
	common = {"branch": branch, "cost_center": cost_center}
	je.append("accounts", {"account": "GST - SSM", ("debit_in_account_currency" if debit else "credit_in_account_currency"): amount, **common})
	je.append("accounts", {"account": temp, ("credit_in_account_currency" if debit else "debit_in_account_currency"): amount, **common})
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	return je.name
