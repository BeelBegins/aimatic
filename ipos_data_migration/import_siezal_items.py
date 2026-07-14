import collections
import re
import zipfile
import xml.etree.ElementTree as ET

import frappe
from frappe.model.naming import make_autoname

# --- Edit these before each run -------------------------------------------------
FILE_PATH = "/home/nabeel/frappe-bench/sites/siezal/public/files/itemasterghuritown.xlsx"
STOCK_UOM = "Pcs"
WAREHOUSE = "Ghouri Town Phase V - SSM"
SELLING_PRICE_LIST = "Standard Selling"
ITEM_NAMING_SERIES = "STO-ITEM-.YYYY.-"
STOCK_ENTRY_CHUNK_SIZE = 200
FALLBACK_ITEM_GROUP = "Products"
ROOT_ITEM_GROUP = "All Item Groups"

# Legacy typos fixed on the way in (see apps/aimatic/ipos_data_migration/import.md, "Category /
# Item Group mapping"). Keyed by the raw upper-case source value.
TYPO_FIX = {
    "PACKEGES": "PACKAGES",
    "ELECTONIC ITEMS": "ELECTRONIC ITEMS",
}

# Category names that are legacy export junk, not real categories -- items
# tagged with these fall back to FALLBACK_ITEM_GROUP instead of building a
# category node around them.
JUNK_CATEGORY_NAMES = {"Test Items"}

# Sibling subcategories merged into one node because they mean the same thing
# (reviewed manually, not auto-merged by string similarity -- see import.md).
# Applied to the normalized (title-cased) subcategory name before the
# majority-vote parent resolution runs, so the merged group's parent is
# whichever category the *combined* rows most often co-occur with.
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
            # Skip spreadsheet footer/totals rows: siezal's source file has a
            # trailing row whose only populated cell is a formula string
            # (e.g. Stock_Value = "=SUBTOTAL(9,S2:S16491)") -- every real
            # column is blank, so it isn't a product row at all. Confirmed by
            # both ItemCode and Description being blank; a real row always
            # has at least one of these.
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
    """Title-case a legacy all-caps category/description string, fixing known
    typos, and treating a literal "NULL" export artifact as blank."""
    s = str(raw or "").strip()
    if not s or s.upper() == "NULL":
        return None
    s = TYPO_FIX.get(s.upper(), s)
    return s.title()


def build_category_tree(rows):
    """Resolve every row's (CatName, SubCatName) into one final Item Group
    leaf name, handling: Proper Case + typo fixes, subcategories that appear
    under more than one parent in the raw data (majority vote by row count),
    and the manually-reviewed near-duplicate merges in SUBCAT_MERGE. See
    apps/aimatic/ipos_data_migration/import.md, "Category / Item Group mapping"."""
    parent_counts = collections.defaultdict(collections.Counter)

    for row in rows:
        cat = normalize_name(row.get("CatName"))
        subcat = normalize_name(row.get("SubCatName"))
        if not subcat:
            continue
        subcat = SUBCAT_MERGE.get(subcat, subcat)
        if cat and cat not in JUNK_CATEGORY_NAMES:
            parent_counts[subcat][cat] += 1

    subcat_parent = {}
    for subcat, counter in parent_counts.items():
        if counter:
            subcat_parent[subcat] = counter.most_common(1)[0][0]

    def resolve_item_group(row):
        cat = normalize_name(row.get("CatName"))
        subcat = normalize_name(row.get("SubCatName"))
        if subcat:
            subcat = SUBCAT_MERGE.get(subcat, subcat)
            if subcat in subcat_parent:
                return subcat
        if cat and cat not in JUNK_CATEGORY_NAMES:
            return cat
        return FALLBACK_ITEM_GROUP

    # all_parents must include every category that wins at least one
    # subcategory's majority vote, PLUS every category used directly as a
    # row's own item_group (row has no subcategory, or an unresolved one) --
    # a category can need a tree node for the second reason without ever
    # winning a subcategory vote (e.g. "Packages" on siezal: 7 items with no
    # subcategory at all, so it never appears as any subcat's parent).
    all_parents = set(subcat_parent.values())
    for row in rows:
        cat = normalize_name(row.get("CatName"))
        subcat = normalize_name(row.get("SubCatName"))
        if subcat:
            subcat = SUBCAT_MERGE.get(subcat, subcat)
            if subcat in subcat_parent:
                continue
        if cat and cat not in JUNK_CATEGORY_NAMES:
            all_parents.add(cat)
    all_parents = sorted(all_parents)
    all_subcats = sorted(subcat_parent.keys())

    return all_parents, all_subcats, subcat_parent, resolve_item_group


def create_item_group_tree(all_parents, all_subcats, subcat_parent):
    existing = set(frappe.get_all("Item Group", pluck="name"))
    created = 0

    for parent in all_parents:
        if parent in existing:
            continue
        doc = frappe.new_doc("Item Group")
        doc.item_group_name = parent
        doc.parent_item_group = ROOT_ITEM_GROUP
        doc.is_group = 1
        doc.insert(ignore_permissions=True)
        existing.add(parent)
        created += 1

    for subcat in all_subcats:
        if subcat in existing:
            continue
        parent = subcat_parent[subcat]
        doc = frappe.new_doc("Item Group")
        doc.item_group_name = subcat
        doc.parent_item_group = parent
        doc.is_group = 0
        doc.insert(ignore_permissions=True)
        existing.add(subcat)
        created += 1

    frappe.db.commit()
    print(f"Item Group tree: {len(all_parents)} categories, {len(all_subcats)} subcategories, {created} newly created")


def get_existing_barcodes():
    data = frappe.get_all("Item Barcode", fields=["barcode", "parent"])
    return {row.barcode: row.parent for row in data if row.barcode}


def resolve_mrp(row):
    """Item.custom_mrp fallback chain: source MRP if nonzero; else rp x 1.18 if
    rp is nonzero; else leave whatever is in the source as-is (0/blank) -- no
    further fallback to Slprice or anything else. See import.md, "Pricing
    mapping"."""
    mrp = as_float(row.get("MRP"))
    if mrp:
        return mrp
    rp = as_float(row.get("rp"))
    if rp:
        return round(rp * 1.18, 2)
    return mrp


def create_item(row, item_group, fbr_categories, existing_barcodes):
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
            # every row -- see apps/aimatic/ipos_data_migration/import.md's hsm-9,629-rows note.
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
    """Genuine negative stock (see import.md) imported as a Material Issue.
    An outgoing entry with no prior valuation history won't accept a rate from
    the row itself, so Item.valuation_rate is set first as a fallback source."""
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
    print(f"Loaded {len(rows)} rows from {FILE_PATH}")

    all_parents, all_subcats, subcat_parent, resolve_item_group = build_category_tree(rows)
    create_item_group_tree(all_parents, all_subcats, subcat_parent)

    fbr_categories = set(frappe.get_all("FBR Tax Category", pluck="name"))
    existing_barcodes = get_existing_barcodes()

    stats = collections.Counter()
    stock_rows = []
    negative_stock_rows = []
    failures = []

    for index, row in enumerate(rows, start=1):
        try:
            item_group = resolve_item_group(row)
            item_name, created_new, skip_reason = create_item(row, item_group, fbr_categories, existing_barcodes)
            if skip_reason:
                stats["skipped_items"] += 1
                failures.append({"row": row["_rownum"], "description": row.get("Description"), "reason": skip_reason})
                continue

            stats["items_created" if created_new else "items_reused"] += 1

            if create_selling_price(item_name, as_float(row.get("Slprice"))):
                stats["selling_prices_created"] += 1

            qty = as_float(row.get("Onhand"))
            rate = as_float(row.get("CurCost"))
            if qty > 0:
                stock_rows.append({"item_code": item_name, "qty": qty, "rate": rate})
                stats["stock_rows_prepared"] += 1
            elif qty < 0:
                # Genuine negative stock, confirmed for siezal -- import as a
                # Material Issue so totals match the source file's own
                # SUM(Onhand*CurCost). Negative stock explicitly allowed
                # (Stock Settings.allow_negative_stock=1, set before this run).
                negative_stock_rows.append({"item_code": item_name, "qty": abs(qty), "rate": rate})
                stats["negative_stock_rows_prepared"] += 1

            if index % 500 == 0:
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
