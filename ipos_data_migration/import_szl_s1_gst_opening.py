"""One-off follow-up to import_szl_s1_stock.py: post the GST portion that was
deliberately backed out of the legacy CurCost before using it as the Stock
Entry valuation rate (see import.md, "Tax-exclusive valuation rate"). That
GST was never recorded anywhere by the main stock import -- per explicit
decision 2026-08-02, it's carried forward as an opening balance on the
existing 'GST - SSM' account (Liability, Duties and Taxes) rather than
discarded, via one Journal Entry against the same Temporary Opening suspense
account the stock and vendor opening balances already use.

Run via bench console:
    exec(open("apps/aimatic/ipos_data_migration/import_szl_s1_gst_opening.py").read(), globals())
"""

import re
import xml.etree.ElementTree as ET
import zipfile

import frappe

TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/ItemData&Onhand.xlsx"
WAREHOUSE = "S1 - Ghouri Town VIP - SSM"
POSTING_DATE = "2026-08-02"
GST_ACCOUNT = "GST - SSM"
TAG = "S1-ONHAND-GST-OPENING-2026-08-02"
# Same known non-product row excluded by the main stock import.
KNOWN_JUNK_DESCRIPTIONS = {"TEST3"}
DRY_RUN = False

NS = {
	"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
	"pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_to_idx(col):
	value = 0
	for ch in col:
		value = value * 26 + (ord(ch) - 64)
	return value - 1


def parse_sheet1_rows(path):
	with zipfile.ZipFile(path) as zf:
		shared_strings = []
		if "xl/sharedStrings.xml" in zf.namelist():
			root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
			for si in root.findall("a:si", NS):
				shared_strings.append("".join(t.text or "" for t in si.iterfind(".//a:t", NS)))
		workbook = ET.fromstring(zf.read("xl/workbook.xml"))
		rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
		rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("pr:Relationship", NS)}
		first_sheet = workbook.find("a:sheets/a:sheet", NS)
		target = rel_map[
			first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
		]
		if not target.startswith("xl/"):
			target = f"xl/{target}"
		worksheet = ET.fromstring(zf.read(target))
		header = None
		rows = []
		for row in worksheet.findall("a:sheetData/a:row", NS):
			values_by_index = {}
			max_index = -1
			for cell in row.findall("a:c", NS):
				ref = cell.attrib["r"]
				col = re.match(r"[A-Z]+", ref).group(0)
				idx = col_to_idx(col)
				max_index = max(max_index, idx)
				cell_type = cell.attrib.get("t")
				value_node = cell.find("a:v", NS)
				if value_node is None:
					value = ""
				elif cell_type == "s":
					value = shared_strings[int(value_node.text)]
				else:
					value = value_node.text or ""
				values_by_index[idx] = value
			values = [values_by_index.get(i, "") for i in range(max_index + 1)]
			if header is None:
				header = values
				continue
			if not any(str(v).strip() for v in values):
				continue
			record = {header[i]: values[i] if i < len(values) else "" for i in range(len(header))}
			if not str(record.get("ItemCode", "")).strip() and not str(record.get("Description", "")).strip():
				continue
			rows.append(record)
		return rows


def as_float(value):
	text = str(value or "").strip()
	if not text:
		return 0.0
	return float(text)


def get_item_tax_rate(item_code, tax_rate_cache):
	if item_code not in tax_rate_cache:
		category = frappe.db.get_value("Item", item_code, "custom_fbr_tax_category")
		tax_rate_cache[item_code] = (
			as_float(frappe.db.get_value("FBR Tax Category", category, "tax_rate")) if category else 0.0
		)
	return tax_rate_cache[item_code]


def exclusive_rate(inclusive_rate, tax_rate):
	if inclusive_rate <= 0 or tax_rate <= 0:
		return inclusive_rate
	sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
	return round(inclusive_rate - sales_tax, 2)


def get_existing_barcodes():
	data = frappe.get_all("Item Barcode", fields=["barcode", "parent"])
	return {row.barcode: row.parent for row in data if row.barcode}


def resolve_item(row, existing_barcodes):
	first = str(row.get("ItemCode", "")).strip()
	second = str(row.get("RefCode", "")).strip()
	for barcode in (first, second):
		if barcode and barcode in existing_barcodes:
			return existing_barcodes[barcode]
	return None


def get_branch_and_cost_center():
	branch = frappe.get_cached_value("Warehouse", WAREHOUSE, "custom_branch")
	cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
	return branch, cost_center


def get_temp_opening_account():
	company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
	return frappe.db.get_value(
		"Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
	), company


def run():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(
			f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'."
		)

	if frappe.db.exists("Journal Entry", {"cheque_no": TAG, "docstatus": ["!=", 2]}):
		print("Already run -- Journal Entry with this tag already exists. Nothing to do.")
		return

	rows = parse_sheet1_rows(FILE_PATH)
	existing_barcodes = get_existing_barcodes()
	tax_rate_cache = {}

	total_gst = 0.0
	matched_rows = 0
	unresolved_rows = []

	for row in rows:
		description = str(row.get("Description", "")).strip()
		if description in KNOWN_JUNK_DESCRIPTIONS:
			continue

		item_code = resolve_item(row, existing_barcodes)
		if not item_code:
			unresolved_rows.append(row.get("_rownum"))
			continue

		qty = as_float(row.get("Onhand"))  # signed: positive = Material Receipt, negative = Material Issue
		inclusive_rate = as_float(row.get("CurCost"))
		tax_rate = get_item_tax_rate(item_code, tax_rate_cache)
		excl_rate = exclusive_rate(inclusive_rate, tax_rate)

		# Signed so a negative-Onhand row's GST correctly nets out too --
		# matches how its Material Issue reduced stock in the main import.
		total_gst += qty * (inclusive_rate - excl_rate)
		matched_rows += 1

	total_gst = round(total_gst, 2)
	print(f"*** DRY_RUN = {DRY_RUN} *** Matched rows: {matched_rows}, unresolved: {len(unresolved_rows)}")
	print(f"Total GST to post (Temporary Opening -> {GST_ACCOUNT}): {total_gst}")

	if DRY_RUN:
		print("DRY_RUN is True -- no Journal Entry created. Set DRY_RUN = False to run for real.")
		return

	if not total_gst:
		print("Total GST rounds to 0 -- nothing to post.")
		return

	branch, cost_center = get_branch_and_cost_center()
	temp_opening_account, company = get_temp_opening_account()
	if not temp_opening_account:
		frappe.throw(f"No 'Temporary Opening' account found under {company}.")

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Opening Entry"
	je.company = company
	je.posting_date = POSTING_DATE
	je.is_opening = "Yes"
	je.cheque_no = TAG
	je.cheque_date = POSTING_DATE
	je.user_remark = (
		"S1 opening stock: GST portion backed out of legacy CurCost before stock valuation (see import.md)"
	)

	amount = abs(total_gst)
	if total_gst > 0:
		je.append(
			"accounts",
			{
				"account": GST_ACCOUNT,
				"debit_in_account_currency": amount,
				"branch": branch,
				"cost_center": cost_center,
			},
		)
		je.append(
			"accounts",
			{
				"account": temp_opening_account,
				"credit_in_account_currency": amount,
				"branch": branch,
				"cost_center": cost_center,
			},
		)
	else:
		je.append(
			"accounts",
			{
				"account": GST_ACCOUNT,
				"credit_in_account_currency": amount,
				"branch": branch,
				"cost_center": cost_center,
			},
		)
		je.append(
			"accounts",
			{
				"account": temp_opening_account,
				"debit_in_account_currency": amount,
				"branch": branch,
				"cost_center": cost_center,
			},
		)

	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.commit()
	print(f"Created and submitted {je.name} for {total_gst}")


run()
