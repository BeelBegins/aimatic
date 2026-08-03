"""Fix for a real gap found in import_szl_s1_stock.py's live run (2026-08-02):
of the 164 originally-unmatched rows, only 156 ended up with both an Item AND
stock actually posted. Root cause, confirmed by comparing item counts across
the three run logs:

- Run 1 and Run 2 both hit the tabSeries-not-seeded bug (see import.md) on
  EVERY one of the 164 new-item rows -- so by the time Run 2 finished posting
  all 69 positive + 13 negative Stock Entry chunks, none of the 164 new items
  existed yet, and their rows were correctly excluded from those chunks
  (confirmed: Run 2's own stats show 13,609/2,573 rows posted, exactly
  13,757/2,589 minus the 164 unmatched rows split by sign).
- Run 3 (after seeding tabSeries) successfully created 163 of the 164 new
  items -- but create_stock_entries()/create_negative_stock_entries() saw
  every chunk tag already present (from Run 2) and skipped creating anything
  new, so these 163 items' own Onhand was never posted at all.
- Of those 163, 7 (all "FRAME ..." items, rows 1057-1068) were themselves
  lost: a later row in the same uncommitted batch (row 1634, "TEST3", a
  genuine junk row) raised an exception, and the script's own
  `frappe.db.rollback()` on that exception rolled back everything since the
  last periodic commit checkpoint (every 1000 rows) -- including these 7
  Items' just-inserted rows, which hadn't been committed yet. So they don't
  exist in the DB at all, despite the run's own stats claiming "items_created:
  163". Confirmed via direct query: item count only grew by 156, and no
  Item Barcode row exists for any of their 7 barcodes.

This script: (1) creates those 7 still-missing Items (same logic as the main
script), (2) posts one Material Receipt + one Material Issue covering every
one of the 163 new items' Onhand that was never actually recorded, checked
directly against Bin rather than trusted from any prior run's own stats.

Run via bench console:
    exec(open("apps/aimatic/ipos_data_migration/import_szl_s1_stock_fix_gap.py").read(), globals())
"""

import collections
import re
import zipfile
import xml.etree.ElementTree as ET

import frappe
from frappe.model.naming import make_autoname

TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/ItemData&Onhand.xlsx"
STOCK_UOM = "Pcs"
WAREHOUSE = "S1 - Ghouri Town VIP - SSM"
SELLING_PRICE_LIST = "S1 - Ghouri Town VIP Selling Price List"
ITEM_NAMING_SERIES = "STO-ITEM-.YYYY.-"
FALLBACK_ITEM_GROUP = "Products"
ROOT_ITEM_GROUP = "All Item Groups"
POSTING_DATE = "2026-08-02"
POSITIVE_TAG = "S1-ONHAND-IMPORT-2026-08-02 fix-gap-positive"
NEGATIVE_TAG = "S1-ONHAND-IMPORT-2026-08-02 fix-gap-negative"
KNOWN_JUNK_DESCRIPTIONS = {"TEST3"}
DRY_RUN = False

TYPO_FIX = {
    "PACKEGES": "PACKAGES",
    "ELECTONIC ITEMS": "ELECTRONIC ITEMS",
}
JUNK_CATEGORY_NAMES = {"Test Items"}
SUBCAT_MERGE = {
    "Household Sundries": "Household Essentials",
    "Watch": "Wrist Watches",
}

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
        target = rel_map[first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
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
            record["_rownum"] = int(row.attrib.get("r", "0"))
            if not str(record.get("ItemCode", "")).strip() and not str(record.get("Description", "")).strip():
                continue
            rows.append(record)
        return rows


def as_float(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    return float(text)


def normalize_name(raw):
    s = str(raw or "").strip()
    if not s or s.upper() == "NULL":
        return None
    s = TYPO_FIX.get(s.upper(), s)
    return s.title()


def resolve_item_group(row):
    cat = normalize_name(row.get("CatName"))
    subcat = normalize_name(row.get("SubCatName"))
    if subcat:
        subcat = SUBCAT_MERGE.get(subcat, subcat)
        if frappe.db.exists("Item Group", subcat):
            return subcat
    if cat and cat not in JUNK_CATEGORY_NAMES and frappe.db.exists("Item Group", cat):
        return cat
    return FALLBACK_ITEM_GROUP


def resolve_mrp(row):
    mrp = as_float(row.get("MRP"))
    if mrp:
        return mrp
    rp = as_float(row.get("rp"))
    if rp:
        return round(rp * 1.18, 2)
    return mrp


def get_existing_barcodes():
    data = frappe.get_all("Item Barcode", fields=["barcode", "parent"])
    return {row.barcode: row.parent for row in data if row.barcode}


def create_item(row, fbr_categories, existing_barcodes):
    first_barcode = str(row.get("ItemCode", "")).strip()
    second_barcode = str(row.get("RefCode", "")).strip()

    matched_items = {
        existing_barcodes[barcode]
        for barcode in (first_barcode, second_barcode)
        if barcode and barcode in existing_barcodes
    }
    if len(matched_items) > 1:
        return None, f"barcodes already exist on multiple items: {sorted(matched_items)}"
    if matched_items:
        return next(iter(matched_items)), None

    item_group = resolve_item_group(row)
    item = frappe.new_doc("Item")
    item.item_code = make_autoname(ITEM_NAMING_SERIES)
    item.naming_series = ITEM_NAMING_SERIES
    item.item_name = normalize_name(row.get("Description")) or str(row.get("Description", "")).strip()
    item.item_group = item_group
    item.stock_uom = STOCK_UOM
    item.is_stock_item = 1
    item.is_sales_item = 1
    item.is_purchase_item = 1
    item.allow_negative_stock = 1

    mrp = resolve_mrp(row)
    if mrp:
        item.custom_mrp = mrp

    fbr_category = str(row.get("Fbr_Tax_Category", "")).strip()
    if fbr_category and fbr_category in fbr_categories:
        item.custom_fbr_tax_category = fbr_category

    if first_barcode:
        item.append("barcodes", {"barcode": first_barcode})
    if second_barcode and second_barcode != first_barcode:
        item.append("barcodes", {"barcode": second_barcode})

    item.insert(ignore_permissions=True)
    frappe.db.commit()

    for barcode in (first_barcode, second_barcode):
        if barcode:
            existing_barcodes[barcode] = item.name

    selling_rate = as_float(row.get("Slprice"))
    if selling_rate > 0 and not frappe.db.exists(
        "Item Price", {"item_code": item.name, "price_list": SELLING_PRICE_LIST, "selling": 1}
    ):
        price = frappe.new_doc("Item Price")
        price.item_code = item.name
        price.price_list = SELLING_PRICE_LIST
        price.selling = 1
        price.price_list_rate = selling_rate
        price.insert(ignore_permissions=True)
        frappe.db.commit()

    return item.name, None


def get_item_tax_rate(item_code, tax_rate_cache):
    if item_code not in tax_rate_cache:
        category = frappe.db.get_value("Item", item_code, "custom_fbr_tax_category")
        tax_rate_cache[item_code] = (
            as_float(frappe.db.get_value("FBR Tax Category", category, "tax_rate"))
            if category
            else 0.0
        )
    return tax_rate_cache[item_code]


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
    return frappe.db.get_value(
        "Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
    )


def items_missing_stock(item_codes):
    """Items that exist but have no Bin row at all for this warehouse yet --
    checked directly, not trusted from any prior run's own printed stats."""
    with_stock = set(
        frappe.get_all(
            "Bin", filters={"warehouse": WAREHOUSE, "item_code": ["in", list(item_codes)]}, pluck="item_code"
        )
    )
    return [code for code in item_codes if code not in with_stock]


def run():
    if frappe.local.site != TARGET_SITE:
        frappe.throw(f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'.")

    rows = parse_sheet1_rows(FILE_PATH)
    fbr_categories = set(frappe.get_all("FBR Tax Category", pluck="name"))
    existing_barcodes = get_existing_barcodes()
    tax_rate_cache = {}

    resolved = []
    created_now = 0
    failures = []

    for row in rows:
        description = str(row.get("Description", "")).strip()
        if description in KNOWN_JUNK_DESCRIPTIONS:
            continue

        first = str(row.get("ItemCode", "")).strip()
        second = str(row.get("RefCode", "")).strip()
        already_matched = (first and first in existing_barcodes) or (second and second in existing_barcodes)

        if DRY_RUN:
            if not already_matched:
                resolved.append({"item_code": None, "row": row})
            continue

        item_name, err = create_item(row, fbr_categories, existing_barcodes)
        if err:
            failures.append({"row": row["_rownum"], "description": description, "reason": err})
            continue
        if not already_matched:
            created_now += 1
        resolved.append({"item_code": item_name, "row": row})

    if DRY_RUN:
        print(f"Would need to create {len(resolved)} still-missing Items (not counting {len(failures)} failures).")
        print("DRY_RUN is True -- no records were created. Set DRY_RUN = False to run for real.")
        return

    print(f"Created {created_now} still-missing Items this run. Failures: {len(failures)}")
    for f in failures:
        print("FAILURE", f)

    item_codes = [r["item_code"] for r in resolved]
    missing = set(items_missing_stock(item_codes))
    print(f"Items with no Bin row for {WAREHOUSE} yet (need a stock entry): {len(missing)} of {len(item_codes)}")

    positive_rows = []
    negative_rows = []
    for r in resolved:
        if r["item_code"] not in missing:
            continue
        row = r["row"]
        qty = as_float(row.get("Onhand"))
        rate = exclusive_rate(as_float(row.get("CurCost")), get_item_tax_rate(r["item_code"], tax_rate_cache))
        if qty > 0:
            positive_rows.append({"item_code": r["item_code"], "qty": qty, "rate": rate})
        elif qty < 0:
            negative_rows.append({"item_code": r["item_code"], "qty": abs(qty), "rate": rate})

    print(f"Positive rows to post: {len(positive_rows)}; negative rows to post: {len(negative_rows)}")

    branch, cost_center = get_branch_and_cost_center()
    temp_opening_account = get_temp_opening_account()
    if not temp_opening_account:
        frappe.throw("No 'Temporary Opening' account found.")

    if positive_rows and not frappe.db.exists("Stock Entry", {"remarks": POSITIVE_TAG}):
        entry = frappe.new_doc("Stock Entry")
        entry.stock_entry_type = "Material Receipt"
        entry.to_warehouse = WAREHOUSE
        entry.posting_date = POSTING_DATE
        entry.set_posting_time = 1
        entry.branch = branch
        entry.remarks = POSITIVE_TAG
        for r in positive_rows:
            entry.append(
                "items",
                {
                    "item_code": r["item_code"],
                    "qty": r["qty"],
                    "t_warehouse": WAREHOUSE,
                    "basic_rate": r["rate"],
                    "valuation_rate": r["rate"],
                    "allow_zero_valuation_rate": 1 if r["rate"] <= 0 else 0,
                    "expense_account": temp_opening_account,
                    "cost_center": cost_center,
                    "branch": branch,
                },
            )
        entry.insert(ignore_permissions=True)
        entry.submit()
        frappe.db.commit()
        print(f"Created {entry.name} with {len(positive_rows)} rows ({POSITIVE_TAG})")
    elif positive_rows:
        print("Positive fix-gap Stock Entry already exists -- skipped.")

    if negative_rows:
        for r in negative_rows:
            if r["rate"] > 0:
                frappe.db.set_value("Item", r["item_code"], "valuation_rate", r["rate"])
        frappe.db.commit()

        if not frappe.db.exists("Stock Entry", {"remarks": NEGATIVE_TAG}):
            entry = frappe.new_doc("Stock Entry")
            entry.stock_entry_type = "Material Issue"
            entry.from_warehouse = WAREHOUSE
            entry.posting_date = POSTING_DATE
            entry.set_posting_time = 1
            entry.branch = branch
            entry.remarks = NEGATIVE_TAG
            for r in negative_rows:
                entry.append(
                    "items",
                    {
                        "item_code": r["item_code"],
                        "qty": r["qty"],
                        "s_warehouse": WAREHOUSE,
                        "basic_rate": r["rate"],
                        "allow_zero_valuation_rate": 1 if r["rate"] <= 0 else 0,
                        "expense_account": temp_opening_account,
                        "cost_center": cost_center,
                        "branch": branch,
                    },
                )
            entry.insert(ignore_permissions=True)
            entry.submit()
            frappe.db.commit()
            print(f"Created {entry.name} with {len(negative_rows)} rows ({NEGATIVE_TAG})")
        else:
            print("Negative fix-gap Stock Entry already exists -- skipped.")


run()
