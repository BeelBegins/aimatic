"""Reconcile S1's selling Price List from ItemData&Onhand.xlsx Slprice.

Dry-run by default. Live invocation from ``bench --site szl console``::

    S1_PRICE_UPDATE_LIVE = True
    exec(open("apps/aimatic/ipos_data_migration/update_szl_s1_sale_prices.py").read(), globals())

The source row is matched to Item through either ItemCode or RefCode in
``Item Barcode``. Positive Slprice values are created or corrected; blank/zero
prices and unmatched/ambiguous rows are reported and left unchanged.
"""

import collections
import re
import xml.etree.ElementTree as ET
import zipfile

import frappe

TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/ItemData&Onhand.xlsx"
PRICE_LIST = "S1 - Ghouri Town VIP Selling Price List"
UOM = "Pcs"
COMMIT_EVERY = 250
LIVE = bool(globals().get("S1_PRICE_UPDATE_LIVE", False))
NS = {
	"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
	"pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_to_idx(col):
	value = 0
	for char in col:
		value = value * 26 + ord(char) - 64
	return value - 1


def parse_rows(path):
	with zipfile.ZipFile(path) as archive:
		strings = []
		if "xl/sharedStrings.xml" in archive.namelist():
			root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
			strings = [
				"".join(node.text or "" for node in item.iterfind(".//a:t", NS))
				for item in root.findall("a:si", NS)
			]
		workbook = ET.fromstring(archive.read("xl/workbook.xml"))
		rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
		rel_map = {
			relation.attrib["Id"]: relation.attrib["Target"]
			for relation in rels.findall("pr:Relationship", NS)
		}
		sheet = workbook.find("a:sheets/a:sheet", NS)
		relationship_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
		target = rel_map[sheet.attrib[relationship_key]]
		if not target.startswith("xl/"):
			target = f"xl/{target}"
		worksheet = ET.fromstring(archive.read(target))

		header = None
		rows = []
		for row_node in worksheet.findall("a:sheetData/a:row", NS):
			values_by_index = {}
			max_index = -1
			for cell in row_node.findall("a:c", NS):
				column = re.match(r"[A-Z]+", cell.attrib["r"]).group(0)
				index = col_to_idx(column)
				max_index = max(max_index, index)
				value_node = cell.find("a:v", NS)
				if value_node is None:
					value = ""
				elif cell.attrib.get("t") == "s":
					value = strings[int(value_node.text)]
				else:
					value = value_node.text or ""
				values_by_index[index] = value
			values = [values_by_index.get(index, "") for index in range(max_index + 1)]
			if header is None:
				header = values
				continue
			record = {
				header[index]: values[index] if index < len(values) else "" for index in range(len(header))
			}
			record["_rownum"] = int(row_node.attrib.get("r", "0"))
			if str(record.get("ItemCode", "")).strip() or str(record.get("Description", "")).strip():
				rows.append(record)
		return rows


def as_float(value):
	text = str(value or "").strip()
	return float(text) if text else 0.0


def run():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(f"Locked to site {TARGET_SITE!r}; current site is {frappe.local.site!r}.")
	price_list = frappe.get_doc("Price List", PRICE_LIST)
	if not price_list.enabled or not price_list.selling or price_list.buying:
		frappe.throw(f"Unexpected configuration on Price List {PRICE_LIST!r}.")
	currency = price_list.currency

	barcode_map = collections.defaultdict(set)
	for barcode, parent in frappe.db.sql(
		"SELECT barcode, parent FROM `tabItem Barcode` WHERE barcode IS NOT NULL AND barcode != ''"
	):
		barcode_map[str(barcode).strip()].add(parent)

	prices_by_item = collections.defaultdict(list)
	for name, item_code, rate in frappe.db.sql(
		"SELECT name, item_code, price_list_rate FROM `tabItem Price` WHERE price_list=%s",
		PRICE_LIST,
	):
		prices_by_item[item_code].append({"name": name, "rate": float(rate or 0)})

	stats = collections.Counter()
	actions = []
	issues = []
	resolved_items = set()
	for row in parse_rows(FILE_PATH):
		stats["workbook_rows"] += 1
		rate = as_float(row.get("Slprice"))
		barcodes = [str(row.get(field, "")).strip() for field in ("ItemCode", "RefCode")]
		matched = set()
		for barcode in barcodes:
			if barcode:
				matched.update(barcode_map.get(barcode, set()))
		if len(matched) != 1:
			key = "unmatched" if not matched else "ambiguous"
			stats[key] += 1
			issues.append(
				{
					"row": row["_rownum"],
					"description": row.get("Description"),
					"reason": key,
					"items": sorted(matched),
				}
			)
			continue
		item_code = next(iter(matched))
		if item_code in resolved_items:
			frappe.throw(f"Duplicate resolved Item in source workbook: {item_code}")
		resolved_items.add(item_code)
		if rate <= 0:
			stats["zero_or_blank_slprice"] += 1
			continue

		existing = prices_by_item.get(item_code, [])
		if len(existing) > 1:
			frappe.throw(f"Multiple {PRICE_LIST} Item Prices found for {item_code}")
		if existing:
			if abs(existing[0]["rate"] - rate) < 0.005:
				stats["already_matching"] += 1
			else:
				actions.append(("update", existing[0]["name"], item_code, rate))
				stats["to_update"] += 1
		else:
			actions.append(("create", None, item_code, rate))
			stats["to_create"] += 1

	print(f"LIVE={LIVE} PRICE_LIST={PRICE_LIST!r} CURRENCY={currency}")
	print("PRECHECK", dict(stats))
	for issue in issues[:20]:
		print("ISSUE", issue)
	if not LIVE:
		print("DRY RUN: no Item Price records changed")
		return

	changed = 0
	for action, name, item_code, rate in actions:
		if action == "update":
			frappe.db.set_value("Item Price", name, "price_list_rate", rate, update_modified=True)
		else:
			price = frappe.new_doc("Item Price")
			price.item_code = item_code
			price.price_list = PRICE_LIST
			price.price_list_rate = rate
			price.uom = UOM
			price.currency = currency
			price.selling = 1
			price.insert(ignore_permissions=True)
		changed += 1
		if changed % COMMIT_EVERY == 0:
			frappe.db.commit()
			print(f"Committed {changed}/{len(actions)} price changes")
	frappe.db.commit()
	print(f"LIVE COMPLETE: {changed} price changes committed")


run()
