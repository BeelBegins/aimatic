import collections
import re
import zipfile
import xml.etree.ElementTree as ET

import frappe
from frappe.model.naming import make_autoname


FILE_PATH = "/home/nabeel/frappe-bench/sites/hsm/private/files/hsmitems.xlsx"
ITEM_GROUP = "Products"
STOCK_UOM = "Pcs"
WAREHOUSE = "Hatim Super Market - HSM"
SELLING_PRICE_LIST = "Standard Selling"
ITEM_NAMING_SERIES = "STO-ITEM-.YYYY.-"
STOCK_ENTRY_CHUNK_SIZE = 200

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
            rows.append(record)

        return rows


def as_float(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    return float(text)


def get_existing_barcodes():
    data = frappe.get_all("Item Barcode", fields=["barcode", "parent"])
    return {row.barcode: row.parent for row in data if row.barcode}


def get_item_tax_rate(item_code, tax_rate_cache):
    """Look up an item's FBR tax rate (0 for Exempt/no category), cached per
    item_code since this is called once per stock row and items repeat."""
    if item_code not in tax_rate_cache:
        category = frappe.db.get_value("Item", item_code, "custom_fbr_tax_category")
        tax_rate_cache[item_code] = (
            as_float(frappe.db.get_value("FBR Tax Category", category, "tax_rate"))
            if category
            else 0.0
        )
    return tax_rate_cache[item_code]


def exclusive_rate(inclusive_rate, tax_rate):
    """Back tax out of a tax-inclusive rate, using the exact same formula as
    fbr_pos.tax_calculator.calculate_fbr_item's sales-side reverse calculation
    (sales_tax = inclusive_value * tax_rate / (100 + tax_rate)) -- source
    CurCost is tax-inclusive. Exempt items (tax_rate 0) pass through
    unchanged. NOTE: this fix was added 2026-07-16, after hsm's own import
    already ran with the unfixed (tax-inclusive) rate -- this correction
    only protects a future re-run or a new site copying this script, it does
    not retroactively correct hsm's already-imported stock valuation."""
    if inclusive_rate <= 0 or tax_rate <= 0:
        return inclusive_rate
    sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
    return round(inclusive_rate - sales_tax, 2)


def create_item(row, fbr_categories, existing_barcodes):
    first_barcode = str(row.get("ItemCode", "")).strip()
    second_barcode = str(row.get("RefCode", "")).strip()

    matched_items = {
        existing_barcodes[barcode]
        for barcode in (first_barcode, second_barcode)
        if barcode and barcode in existing_barcodes
    }
    if len(matched_items) > 1:
        return None, False, f"barcodes already exist on multiple items: {sorted(matched_items)}"
    if matched_items:
        return next(iter(matched_items)), False, None

    item = frappe.new_doc("Item")
    item.item_code = make_autoname(ITEM_NAMING_SERIES)
    item.naming_series = ITEM_NAMING_SERIES
    item.item_name = str(row.get("Description", "")).strip()
    item.item_group = ITEM_GROUP
    item.stock_uom = STOCK_UOM
    item.is_stock_item = 1
    item.is_sales_item = 1
    item.is_purchase_item = 1
    item.allow_negative_stock = 1

    mrp = as_float(row.get("MRP"))
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

    for barcode in (first_barcode, second_barcode):
        if barcode:
            existing_barcodes[barcode] = item.name

    return item.name, True, None


def create_selling_price(item_code, selling_rate):
    if selling_rate <= 0:
        return False

    if frappe.db.exists(
        "Item Price",
        {"item_code": item_code, "price_list": SELLING_PRICE_LIST, "selling": 1},
    ):
        return False

    price = frappe.new_doc("Item Price")
    price.item_code = item_code
    price.price_list = SELLING_PRICE_LIST
    price.selling = 1
    price.price_list_rate = selling_rate
    price.insert(ignore_permissions=True)
    return True


def create_stock_entries(stock_rows):
    created = 0
    zero_rate_rows = 0
    for start in range(0, len(stock_rows), STOCK_ENTRY_CHUNK_SIZE):
        chunk = stock_rows[start : start + STOCK_ENTRY_CHUNK_SIZE]
        if not chunk:
            continue

        entry = frappe.new_doc("Stock Entry")
        entry.stock_entry_type = "Material Receipt"
        entry.to_warehouse = WAREHOUSE
        entry.set_posting_time = 1

        for stock_row in chunk:
            # allow_zero_valuation_rate must be conditional, not blanket-on for
            # every row -- set unconditionally, it would silently accept a rate
            # of 0 even for items whose real CurCost failed to make it through
            # (exactly the bug that produced 9,629 zero-valued stock rows on the
            # first hsm import run: nothing ever complained). Rows with a real
            # nonzero rate must fail loudly if that rate is ever lost upstream.
            entry.append(
                "items",
                {
                    "item_code": stock_row["item_code"],
                    "qty": stock_row["qty"],
                    "t_warehouse": WAREHOUSE,
                    "basic_rate": stock_row["rate"],
                    "valuation_rate": stock_row["rate"],
                    "allow_zero_valuation_rate": 1 if stock_row["rate"] <= 0 else 0,
                },
            )
            if stock_row["rate"] <= 0:
                zero_rate_rows += 1

        entry.insert(ignore_permissions=True)
        entry.submit()
        created += 1
        frappe.db.commit()
        print(f"Created stock entry {entry.name} with {len(chunk)} rows")

    print(f"Stock rows with a genuinely zero rate (allow_zero_valuation_rate=1): {zero_rate_rows} of {len(stock_rows)}")
    return created


def create_negative_stock_entries(stock_rows):
    """stock_rows: {item_code, qty (positive magnitude), rate} for legacy rows
    where Onhand was negative -- genuine negative stock in the source system,
    imported as a Material Issue that pushes actual_qty below zero (every item
    already has allow_negative_stock=1 from create_item).

    Unlike Material Receipt, an outgoing entry with no prior stock/valuation
    history will NOT accept a rate from the row itself -- ERPNext raises
    "Valuation Rate for the Item ... is required" instead of using basic_rate,
    because outgoing rate is normally derived from existing valuation history,
    and here there is none. Setting Item.valuation_rate first (verified via a
    manual console test before this was written) gives it a fallback to draw
    on, and the row's own basic_rate then correctly becomes the posted rate.
    """
    for stock_row in stock_rows:
        if stock_row["rate"] > 0:
            frappe.db.set_value("Item", stock_row["item_code"], "valuation_rate", stock_row["rate"])
    frappe.db.commit()

    created = 0
    zero_rate_rows = 0
    for start in range(0, len(stock_rows), STOCK_ENTRY_CHUNK_SIZE):
        chunk = stock_rows[start : start + STOCK_ENTRY_CHUNK_SIZE]
        if not chunk:
            continue

        entry = frappe.new_doc("Stock Entry")
        entry.stock_entry_type = "Material Issue"
        entry.from_warehouse = WAREHOUSE
        entry.set_posting_time = 1

        for stock_row in chunk:
            entry.append(
                "items",
                {
                    "item_code": stock_row["item_code"],
                    "qty": stock_row["qty"],
                    "s_warehouse": WAREHOUSE,
                    "basic_rate": stock_row["rate"],
                    "allow_zero_valuation_rate": 1 if stock_row["rate"] <= 0 else 0,
                },
            )
            if stock_row["rate"] <= 0:
                zero_rate_rows += 1

        entry.insert(ignore_permissions=True)
        entry.submit()
        created += 1
        frappe.db.commit()
        print(f"Created negative-stock entry {entry.name} with {len(chunk)} rows")

    print(f"Negative-stock rows with a genuinely zero rate: {zero_rate_rows} of {len(stock_rows)}")
    return created


def run():
    rows = parse_sheet1_rows(FILE_PATH)
    fbr_categories = set(frappe.get_all("FBR Tax Category", pluck="name"))
    existing_barcodes = get_existing_barcodes()

    stats = collections.Counter()
    stock_rows = []
    negative_stock_rows = []
    failures = []
    tax_rate_cache = {}

    for index, row in enumerate(rows, start=1):
        try:
            item_name, created_new, skip_reason = create_item(row, fbr_categories, existing_barcodes)
            if skip_reason:
                stats["skipped_items"] += 1
                failures.append({"row": row["_rownum"], "description": row.get("Description"), "reason": skip_reason})
                continue

            stats["items_created" if created_new else "items_reused"] += 1

            if create_selling_price(item_name, as_float(row.get("Slprice"))):
                stats["selling_prices_created"] += 1

            qty = as_float(row.get("Onhand"))
            rate = exclusive_rate(
                as_float(row.get("CurCost")), get_item_tax_rate(item_name, tax_rate_cache)
            )
            if qty > 0:
                stock_rows.append({"item_code": item_name, "qty": qty, "rate": rate})
                stats["stock_rows_prepared"] += 1
            elif qty < 0:
                # Genuine negative stock in the legacy system (verified with the
                # user, not a sign/export artifact) -- imported as a Material
                # Issue so the total matches the source file's full SUM(Onhand*CurCost).
                negative_stock_rows.append({"item_code": item_name, "qty": abs(qty), "rate": rate})
                stats["negative_stock_rows_prepared"] += 1

            if index % 200 == 0:
                frappe.db.commit()
                print(f"Processed {index}/{len(rows)} rows")

        except Exception as exc:
            frappe.db.rollback()
            stats["failed_rows"] += 1
            failures.append({"row": row["_rownum"], "description": row.get("Description"), "reason": str(exc)})
            print(f"FAILED row {row['_rownum']}: {row.get('Description')} -> {exc}")

    frappe.db.commit()

    stock_entries_created = create_stock_entries(stock_rows)
    stats["stock_entries_created"] = stock_entries_created

    negative_stock_entries_created = create_negative_stock_entries(negative_stock_rows)
    stats["negative_stock_entries_created"] = negative_stock_entries_created

    print("SUMMARY", dict(stats))
    print("FAILURE_COUNT", len(failures))
    for failure in failures[:100]:
        print("FAILURE", failure)


run()
