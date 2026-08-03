"""S1 (Ghouri Town VIP) opening stock import on szl, from the legacy
ItemData&Onhand.xlsx workbook (Onhand/CurCost columns). Companion to
import_siezal_items.py, which this is modeled on -- but items themselves are
NOT created wholesale here: szl's Item/Item Barcode catalog was already bulk-
copied from siezal (import_siezal_catalog_to_szl.py), so most rows already
match an existing Item via barcode. Only the small minority with no matching
barcode get a newly created Item (same creation logic as the siezal script).

Run via bench console (this directory has no __init__.py -- not importable
via `bench execute`). `exec(open(...).read())` alone fails with a NameError
on this script's own top-level constants inside IPython's bench console --
must pass globals() explicitly:
    exec(open("apps/aimatic/ipos_data_migration/import_szl_s1_stock.py").read(), globals())
"""

import collections
import re
import zipfile
import xml.etree.ElementTree as ET

import frappe
from frappe.model.naming import make_autoname

# --- Edit these before each run -------------------------------------------------
TARGET_SITE = "szl"
FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/ItemData&Onhand.xlsx"
STOCK_UOM = "Pcs"
WAREHOUSE = "S1 - Ghouri Town VIP - SSM"
SELLING_PRICE_LIST = "S1 - Ghouri Town VIP Selling Price List"
ITEM_NAMING_SERIES = "STO-ITEM-.YYYY.-"
STOCK_ENTRY_CHUNK_SIZE = 200
FALLBACK_ITEM_GROUP = "Products"
ROOT_ITEM_GROUP = "All Item Groups"
# Explicit, not today() -- decided 2026-08-02 before the calendar date moved on.
POSTING_DATE = "2026-08-02"
# Deterministic tags stamped into each Stock Entry's remarks so a re-run after
# an interruption skips chunks already posted, instead of double-posting --
# import_siezal_items.py's own stock-entry phase has no such guard (fine for
# a single clean run there), but this run's console session already got
# killed by a shell timeout once during today's customer import, so the same
# risk is real here too.
POSITIVE_TAG = "S1-ONHAND-IMPORT-2026-08-02"
NEGATIVE_TAG = "S1-ONHAND-NEGATIVE-IMPORT-2026-08-02"
# True -> parse, reconcile and print counts only, no insert/submit/commit.
DRY_RUN = False

# Legacy typos fixed on the way in (see import.md, "Category / Item Group mapping").
TYPO_FIX = {
    "PACKEGES": "PACKAGES",
    "ELECTONIC ITEMS": "ELECTRONIC ITEMS",
}
JUNK_CATEGORY_NAMES = {"Test Items"}
SUBCAT_MERGE = {
    "Household Sundries": "Household Essentials",
    "Watch": "Wrist Watches",
}
# ---------------------------------------------------------------------------------

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


def build_category_tree(rows):
    """Same majority-vote resolution as import_siezal_items.py, but only
    needs to run over the rows that don't already match an existing Item --
    the rest already have an Item Group from the original siezal catalog
    copy."""
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
    print(f"Item Group tree (new-item categories only): {len(all_parents)} categories, {len(all_subcats)} subcategories, {created} newly created")


def get_existing_barcodes():
    data = frappe.get_all("Item Barcode", fields=["barcode", "parent"])
    return {row.barcode: row.parent for row in data if row.barcode}


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
    """Back tax out of a tax-inclusive rate -- source CurCost is tax-inclusive
    (see import.md and import_siezal_items.py's own note on this)."""
    if inclusive_rate <= 0 or tax_rate <= 0:
        return inclusive_rate
    sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
    return round(inclusive_rate - sales_tax, 2)


def resolve_mrp(row):
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


def ensure_stock_prereqs():
    """One-time site-wide corrections, per explicit decision 2026-08-02:
    Stock Settings.default_warehouse was left pointing at the disabled
    generic 'Stores - SSM' warehouse (see the root CLAUDE.md's "no generic
    fallback" policy -- this one slipped through), and valuation_method was
    left on ERPNext's FIFO default instead of the Moving Average this app
    standardizes on. Also clears the same stale generic-warehouse default off
    every Item Default row that still has it (284 of them, confirmed before
    this run). Idempotent -- safe to re-run."""
    settings = frappe.get_single("Stock Settings")
    changed = False
    if settings.default_warehouse:
        print(f"Clearing Stock Settings.default_warehouse (was {settings.default_warehouse!r})")
        settings.default_warehouse = None
        changed = True
    if settings.valuation_method != "Moving Average":
        print(f"Setting Stock Settings.valuation_method to Moving Average (was {settings.valuation_method!r})")
        settings.valuation_method = "Moving Average"
        changed = True
    if not settings.allow_negative_stock:
        print("Enabling Stock Settings.allow_negative_stock (required for the negative-Onhand Material Issue rows)")
        settings.allow_negative_stock = 1
        changed = True
    if changed:
        settings.save(ignore_permissions=True)
        frappe.db.commit()

    stale = frappe.get_all("Item Default", filters={"default_warehouse": "Stores - SSM"}, pluck="name")
    if stale:
        print(f"Clearing default_warehouse on {len(stale)} Item Default rows still pointing at 'Stores - SSM'")
        for name in stale:
            frappe.db.set_value("Item Default", name, "default_warehouse", None)
        frappe.db.commit()


def get_branch_and_cost_center():
    branch = frappe.get_cached_value("Warehouse", WAREHOUSE, "custom_branch")
    if not branch:
        frappe.throw(f"Warehouse {WAREHOUSE} has no Branch mapped (custom_branch is blank).")
    cost_center = frappe.get_cached_value("Branch", branch, "cost_center")
    if not cost_center:
        frappe.throw(f"Branch {branch} has no Cost Center configured.")
    return branch, cost_center


def get_temp_opening_account():
    """The offsetting account for these one-time opening-stock entries must
    NOT be the default Stock Adjustment account (Expense / P&L -- crediting
    it would read as phantom income for whatever period this migration runs
    in). Temporary Opening (Balance Sheet) is the correct wash account -- the
    same one import_siezal_suppliers.py already uses for opening vendor
    balances -- so the two migration halves net through one suspense account
    instead of one leaking into reported profit, per explicit instruction
    2026-08-02."""
    company = frappe.db.get_value("Warehouse", WAREHOUSE, "company")
    account = frappe.db.get_value(
        "Account", {"company": company, "account_name": "Temporary Opening", "is_group": 0}
    )
    if not account:
        frappe.throw(f"No 'Temporary Opening' account found under {company}.")
    return account


def get_disabled_items():
    """Pre-existing disabled Items (copied over from siezal's catalog, e.g.
    the 7 discontinued 'X Off' crockery items found during the first live
    run 2026-08-02) can't receive a Stock Entry -- ERPNext's own validate_item
    throws 'not active'. Rather than crash a whole chunk on the first such
    row, these are skipped and reported so a human decides whether to
    re-enable them (they may have real Onhand in the legacy sheet) or leave
    their stock out of this import."""
    return set(frappe.get_all("Item", filters={"disabled": 1}, pluck="name"))


def create_stock_entries(stock_rows, disabled_items):
    temp_opening_account = get_temp_opening_account()
    branch, cost_center = get_branch_and_cost_center()
    created = 0
    skipped_chunks = 0
    zero_rate_rows = 0
    skipped_disabled = []
    for start in range(0, len(stock_rows), STOCK_ENTRY_CHUNK_SIZE):
        chunk = [r for r in stock_rows[start : start + STOCK_ENTRY_CHUNK_SIZE] if r["item_code"] not in disabled_items]
        skipped_disabled.extend(
            r for r in stock_rows[start : start + STOCK_ENTRY_CHUNK_SIZE] if r["item_code"] in disabled_items
        )
        if not chunk:
            continue
        chunk_index = start // STOCK_ENTRY_CHUNK_SIZE
        tag = f"{POSITIVE_TAG} chunk {chunk_index}"

        if frappe.db.exists("Stock Entry", {"remarks": tag}):
            skipped_chunks += 1
            continue

        entry = frappe.new_doc("Stock Entry")
        entry.stock_entry_type = "Material Receipt"
        entry.to_warehouse = WAREHOUSE
        entry.posting_date = POSTING_DATE
        entry.set_posting_time = 1
        entry.branch = branch
        entry.remarks = tag

        for stock_row in chunk:
            entry.append(
                "items",
                {
                    "item_code": stock_row["item_code"],
                    "qty": stock_row["qty"],
                    "t_warehouse": WAREHOUSE,
                    "basic_rate": stock_row["rate"],
                    "valuation_rate": stock_row["rate"],
                    "allow_zero_valuation_rate": 1 if stock_row["rate"] <= 0 else 0,
                    "expense_account": temp_opening_account,
                    "cost_center": cost_center,
                    "branch": branch,
                },
            )
            if stock_row["rate"] <= 0:
                zero_rate_rows += 1

        entry.insert(ignore_permissions=True)
        entry.submit()
        created += 1
        frappe.db.commit()
        print(f"Created stock entry {entry.name} with {len(chunk)} rows ({tag})")

    print(f"Positive stock: {created} entries created, {skipped_chunks} chunks already present (skipped)")
    print(f"Stock rows with a genuinely zero rate (allow_zero_valuation_rate=1): {zero_rate_rows} of {len(stock_rows)}")
    if skipped_disabled:
        print(f"SKIPPED {len(skipped_disabled)} positive stock rows for disabled Items (needs a human decision):")
        for r in skipped_disabled:
            print("  SKIPPED DISABLED ITEM (positive)", r)
    return created


def create_negative_stock_entries(stock_rows, disabled_items):
    """Genuine negative stock (confirmed expected S1 data, 2026-08-02 --
    2,589 of 16,346 rows). Imported as a Material Issue. An outgoing entry
    with no prior valuation history won't accept a rate from the row itself,
    so Item.valuation_rate is set first as a fallback source (same as
    import_siezal_items.py)."""
    for stock_row in stock_rows:
        if stock_row["rate"] > 0:
            frappe.db.set_value("Item", stock_row["item_code"], "valuation_rate", stock_row["rate"])
    frappe.db.commit()

    temp_opening_account = get_temp_opening_account()
    branch, cost_center = get_branch_and_cost_center()
    created = 0
    skipped_chunks = 0
    zero_rate_rows = 0
    skipped_disabled = []
    for start in range(0, len(stock_rows), STOCK_ENTRY_CHUNK_SIZE):
        chunk = [r for r in stock_rows[start : start + STOCK_ENTRY_CHUNK_SIZE] if r["item_code"] not in disabled_items]
        skipped_disabled.extend(
            r for r in stock_rows[start : start + STOCK_ENTRY_CHUNK_SIZE] if r["item_code"] in disabled_items
        )
        if not chunk:
            continue
        chunk_index = start // STOCK_ENTRY_CHUNK_SIZE
        tag = f"{NEGATIVE_TAG} chunk {chunk_index}"

        if frappe.db.exists("Stock Entry", {"remarks": tag}):
            skipped_chunks += 1
            continue

        entry = frappe.new_doc("Stock Entry")
        entry.stock_entry_type = "Material Issue"
        entry.from_warehouse = WAREHOUSE
        entry.posting_date = POSTING_DATE
        entry.set_posting_time = 1
        entry.branch = branch
        entry.remarks = tag

        for stock_row in chunk:
            entry.append(
                "items",
                {
                    "item_code": stock_row["item_code"],
                    "qty": stock_row["qty"],
                    "s_warehouse": WAREHOUSE,
                    "basic_rate": stock_row["rate"],
                    "allow_zero_valuation_rate": 1 if stock_row["rate"] <= 0 else 0,
                    "expense_account": temp_opening_account,
                    "cost_center": cost_center,
                    "branch": branch,
                },
            )
            if stock_row["rate"] <= 0:
                zero_rate_rows += 1

        entry.insert(ignore_permissions=True)
        entry.submit()
        created += 1
        frappe.db.commit()
        print(f"Created negative-stock entry {entry.name} with {len(chunk)} rows ({tag})")

    print(f"Negative stock: {created} entries created, {skipped_chunks} chunks already present (skipped)")
    print(f"Negative-stock rows with a genuinely zero rate: {zero_rate_rows} of {len(stock_rows)}")
    if skipped_disabled:
        print(f"SKIPPED {len(skipped_disabled)} negative stock rows for disabled Items (needs a human decision):")
        for r in skipped_disabled:
            print("  SKIPPED DISABLED ITEM (negative)", r)
    return created


def run():
    if frappe.local.site != TARGET_SITE:
        frappe.throw(f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'.")

    rows = parse_sheet1_rows(FILE_PATH)
    print(f"*** DRY_RUN = {DRY_RUN} *** Loaded {len(rows)} rows from {FILE_PATH}, posting date {POSTING_DATE}")

    fbr_categories = set(frappe.get_all("FBR Tax Category", pluck="name"))
    existing_barcodes = get_existing_barcodes()

    unmatched_rows = []
    for row in rows:
        first = str(row.get("ItemCode", "")).strip()
        second = str(row.get("RefCode", "")).strip()
        if not ((first and first in existing_barcodes) or (second and second in existing_barcodes)):
            unmatched_rows.append(row)

    print(f"Matched existing Item: {len(rows) - len(unmatched_rows)}; unmatched (new Item needed): {len(unmatched_rows)}")

    if DRY_RUN:
        pos = sum(1 for r in rows if as_float(r.get("Onhand")) > 0)
        neg = sum(1 for r in rows if as_float(r.get("Onhand")) < 0)
        print(f"Would prepare {pos} positive-qty rows and {neg} negative-qty rows.")
        print("DRY_RUN is True -- no records were created. Set DRY_RUN = False to run for real.")
        return

    ensure_stock_prereqs()

    all_parents, all_subcats, subcat_parent, resolve_item_group = build_category_tree(unmatched_rows)
    create_item_group_tree(all_parents, all_subcats, subcat_parent)

    stats = collections.Counter()
    stock_rows = []
    negative_stock_rows = []
    failures = []
    tax_rate_cache = {}

    for index, row in enumerate(rows, start=1):
        try:
            item_group = resolve_item_group(row)
            item_name, created_new, skip_reason = create_item(row, item_group, fbr_categories, existing_barcodes)
            if skip_reason:
                stats["skipped_items"] += 1
                failures.append({"row": row["_rownum"], "description": row.get("Description"), "reason": skip_reason})
                continue

            stats["items_created" if created_new else "items_reused"] += 1

            if created_new and create_selling_price(item_name, as_float(row.get("Slprice"))):
                stats["selling_prices_created"] += 1

            qty = as_float(row.get("Onhand"))
            rate = exclusive_rate(
                as_float(row.get("CurCost")), get_item_tax_rate(item_name, tax_rate_cache)
            )
            if qty > 0:
                stock_rows.append({"item_code": item_name, "qty": qty, "rate": rate})
                stats["stock_rows_prepared"] += 1
            elif qty < 0:
                negative_stock_rows.append({"item_code": item_name, "qty": abs(qty), "rate": rate})
                stats["negative_stock_rows_prepared"] += 1

            if index % 1000 == 0:
                frappe.db.commit()
                print(f"Processed {index}/{len(rows)} rows")

        except Exception as exc:
            frappe.db.rollback()
            stats["failed_rows"] += 1
            failures.append({"row": row["_rownum"], "description": row.get("Description"), "reason": str(exc)})
            print(f"FAILED row {row['_rownum']}: {row.get('Description')} -> {exc}")

    frappe.db.commit()

    disabled_items = get_disabled_items()
    if disabled_items:
        print(f"NOTE: {len(disabled_items)} Items are currently disabled site-wide: {sorted(disabled_items)}")

    stock_entries_created = create_stock_entries(stock_rows, disabled_items)
    stats["stock_entries_created"] = stock_entries_created

    negative_stock_entries_created = create_negative_stock_entries(negative_stock_rows, disabled_items)
    stats["negative_stock_entries_created"] = negative_stock_entries_created

    print("SUMMARY", dict(stats))
    print("FAILURE_COUNT", len(failures))
    for failure in failures[:100]:
        print("FAILURE", failure)


run()
