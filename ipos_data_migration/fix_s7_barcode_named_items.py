"""Fix 5 barcodes from the S7 stock file (1007.xls) that the price/stock and
GST scripts' resolve_item() couldn't find, even though the barcode audit
counted them as "already covered" on szl.

Root cause: each barcode is already an existing, enabled catalog Item --
but with `item_code` literally set to the barcode digits (a pre-`STO-ITEM-
.YYYY.-` naming convention) and *zero* Item Barcode child rows. The audit's
`_exists()` matches on Item.name directly so it saw these as covered; the
price/stock/GST scripts only look at the Item Barcode table, so they missed
them. These are real, distinct existing items (item_group/FBR already set),
not new items -- creating fresh STO-ITEM-* records for these barcodes would
be true duplicates.

Fix: attach an Item Barcode row (barcode = item_code) to each, then post
their S7 price, opening stock, and GST portion the same way the main S7
scripts do. TARGET_SITE hard-locked to szl -- these 5 items and their real
barcodes/prices/onhand were confirmed live 2026-09-06, not mock-tested first
(only 5 rows, same verified logic as the already-run bulk scripts).

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/fix_s7_barcode_named_items.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import frappe

TARGET_SITE = "szl"
WAREHOUSE = "S7 - Empire Heights - SSM"
PRICE_LIST = "S7 - Empire Heights Selling Price List"
GST_ACCOUNT = "GST - SSM"
POSTING_DATE = "2026-09-06"
STOCK_TAG = "S7-ONHAND-IMPORT-2026-09-06 barcode-named-items"
GST_TAG = "S7-ONHAND-GST-OPENING-2026-09-06 barcode-named-items"

# barcode == item_code for every one of these (see docstring).
ROWS = [
	{"item_code": "4005900517982", "onhand": 0.0, "cost": 1243.41, "sale_price": 1549.0},
	{"item_code": "8851932331302", "onhand": 2.0, "cost": 480.0, "sale_price": 625.0},
	{"item_code": "8999999036546", "onhand": 2.0, "cost": 410.0, "sale_price": 625.0},
	{"item_code": "8964003018412", "onhand": 0.0, "cost": 158.79, "sale_price": 189.0},
	{"item_code": "5028217999974", "onhand": 5.0, "cost": 225.6, "sale_price": 239.0},
]


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")


def exclusive_rate(inclusive_rate, tax_rate):
	if inclusive_rate <= 0 or tax_rate <= 0:
		return inclusive_rate
	sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
	return round(inclusive_rate - sales_tax, 2)


def get_branch_and_cost_center():
	branch = frappe.get_cached_value("Warehouse", WAREHOUSE, "custom_branch")
	cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
	return branch, cost_center


def get_temp_opening_account():
	company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
	account = frappe.db.get_value(
		"Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
	)
	return account, company


def main(apply: bool = False):
	_assert_site()
	plan = []
	for row in ROWS:
		code = row["item_code"]
		if not frappe.db.exists("Item", code):
			frappe.throw(f"{code} no longer exists -- re-check before running")
		existing_barcode_rows = frappe.get_all("Item Barcode", filters={"parent": code}, fields=["barcode"])
		if existing_barcode_rows:
			plan.append({**row, "action": "barcode_already_attached"})
			continue
		stock_uom = frappe.db.get_value("Item", code, "stock_uom")
		category = frappe.db.get_value("Item", code, "custom_fbr_tax_category")
		tax_rate = float(frappe.db.get_value("FBR Tax Category", category, "tax_rate") or 0) if category else 0.0
		excl_cost = exclusive_rate(row["cost"], tax_rate)
		plan.append(
			{
				**row,
				"action": "fix",
				"stock_uom": stock_uom,
				"tax_rate": tax_rate,
				"exclusive_cost": excl_cost,
			}
		)
	print({"site": TARGET_SITE, "apply": apply, "plan": plan})
	if not apply:
		print("DRY_RUN -- no changes made.")
		return plan

	branch, cost_center = get_branch_and_cost_center()
	temp_opening_account, company = get_temp_opening_account()

	# 1. Attach barcode.
	for row in plan:
		if row["action"] != "fix":
			continue
		item = frappe.get_doc("Item", row["item_code"])
		item.append("barcodes", {"barcode": row["item_code"], "uom": row["stock_uom"]})
		item.save(ignore_permissions=True)
	frappe.db.commit()
	print("Barcodes attached:", sum(1 for r in plan if r["action"] == "fix"))

	# 2. Post S7 selling price.
	price_count = 0
	for row in plan:
		if row["action"] != "fix" or row["sale_price"] <= 0:
			continue
		if frappe.db.exists("Item Price", {"item_code": row["item_code"], "price_list": PRICE_LIST}):
			continue
		p = frappe.new_doc("Item Price")
		p.item_code = row["item_code"]
		p.price_list = PRICE_LIST
		p.price_list_rate = row["sale_price"]
		p.uom = row["stock_uom"]
		p.currency = "PKR"
		p.selling = 1
		p.insert(ignore_permissions=True)
		price_count += 1
	frappe.db.commit()
	print("Prices posted:", price_count)

	# 3. Post opening stock (only nonzero onhand).
	stock_rows = [r for r in plan if r["action"] == "fix" and r["onhand"] > 0]
	if stock_rows and not frappe.db.exists("Stock Entry", {"remarks": STOCK_TAG}):
		entry = frappe.new_doc("Stock Entry")
		entry.stock_entry_type = "Material Receipt"
		entry.posting_date = POSTING_DATE
		entry.set_posting_time = 1
		entry.to_warehouse = WAREHOUSE
		entry.branch = branch
		entry.cost_center = cost_center
		entry.remarks = STOCK_TAG
		for row in stock_rows:
			entry.append(
				"items",
				{
					"item_code": row["item_code"],
					"qty": row["onhand"],
					"basic_rate": row["exclusive_cost"],
					"valuation_rate": row["exclusive_cost"],
					"allow_zero_valuation_rate": 1 if row["exclusive_cost"] <= 0 else 0,
					"expense_account": temp_opening_account,
					"t_warehouse": WAREHOUSE,
					"cost_center": cost_center,
					"branch": branch,
				},
			)
		entry.insert(ignore_permissions=True)
		entry.submit()
		frappe.db.commit()
		print("Stock Entry posted:", entry.name, "rows:", len(stock_rows))
	else:
		print("Stock Entry skipped (already exists or nothing to post)")

	# 4. Post GST portion (signed, matches import_s7_gst_opening.py's convention).
	total_gst = round(
		sum(r["onhand"] * (r["cost"] - r["exclusive_cost"]) for r in plan if r["action"] == "fix"), 2
	)
	if total_gst and not frappe.db.exists("Journal Entry", {"cheque_no": GST_TAG, "docstatus": ["!=", 2]}):
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Opening Entry"
		je.company = company
		je.posting_date = POSTING_DATE
		je.is_opening = "Yes"
		je.cheque_no = GST_TAG
		je.cheque_date = POSTING_DATE
		je.user_remark = "S7 opening stock GST portion for barcode-named items fixed after main GST post"
		amount = abs(total_gst)
		if total_gst > 0:
			je.append("accounts", {"account": GST_ACCOUNT, "debit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
			je.append("accounts", {"account": temp_opening_account, "credit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
		else:
			je.append("accounts", {"account": GST_ACCOUNT, "credit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
			je.append("accounts", {"account": temp_opening_account, "debit_in_account_currency": amount, "branch": branch, "cost_center": cost_center})
		je.insert(ignore_permissions=True)
		je.submit()
		frappe.db.commit()
		print("GST JE posted:", je.name, "amount:", total_gst)
	else:
		print("GST posting skipped (zero or already posted). total_gst=", total_gst)

	return {"fixed": sum(1 for r in plan if r["action"] == "fix")}
