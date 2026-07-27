"""Adds the items present in ItemData&Onhand.xlsx (S1's own item master
export from the old iPOS software) that aren't already covered by the
catalog already replicated from siezal onto szl (see
import_siezal_catalog_to_szl.py).

Matching is NOT a plain ItemCode == Item.item_code comparison -- this
file's ItemCode/RefCode columns are barcodes, matched against Item Barcode
records (same convention as the existing ipos_data_migration/import.md:
"ItemCode and RefCode being treated as the first and second plain
barcodes"), not against Item.name directly. Of 16,482 rows, only 285 don't
match any existing Item (by item_code) or Item Barcode (by barcode) already
on szl -- those 285 are what this script creates.

Deliberately narrow scope, matching today's "master data only" instruction:
creates the Item master record (item_code, item_name, item_group, brand,
FBR tax category, barcode) for each missing row. Does NOT touch pricing
(CurCost/Slprice/MRP) or stock (QTY/OB/Onhand/Stock_Value) -- those are
explicitly deferred until after the old iPOS software stops selling
(2026-07-28 -> 29 cutover).

Run via bench console (this directory has no __init__.py -- not importable
via `bench execute`):
    exec(open("apps/aimatic/ipos_data_migration/add_missing_items_from_file.py").read())
    main()
"""

import re
import zipfile
import xml.etree.ElementTree as ET

import frappe

FILE_PATH = "/home/nabeel/frappe-bench/sites/szl/private/files/ItemData&Onhand.xlsx"

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def col_to_idx(col):
    value = 0
    for ch in col:
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def parse_first_sheet_rows(path):
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
            rows.append(record)

        return rows


def find_missing_rows():
    rows = parse_first_sheet_rows(FILE_PATH)

    known_item_codes = set(frappe.get_all("Item", pluck="name"))
    known_barcodes = set(frappe.get_all("Item Barcode", pluck="barcode"))
    known = known_item_codes | known_barcodes

    missing = []
    for r in rows:
        item_code = r["ItemCode"].strip()
        ref_code = r["RefCode"].strip()
        if not item_code:
            continue
        if item_code in known:
            continue
        if ref_code and ref_code in known:
            continue
        missing.append(r)

    return missing


def resolve_item_group(subcat_name):
    """Case-insensitive match against existing Item Group names -- the
    file's category names are ALL CAPS, the imported tree from siezal uses
    Title Case, but otherwise match exactly."""
    name = subcat_name.strip()
    if not name:
        return None
    existing = frappe.db.get_value("Item Group", {"item_group_name": name})
    if existing:
        return existing
    # fallback: case-insensitive lookup
    match = frappe.db.sql(
        "select name from `tabItem Group` where lower(item_group_name) = %s limit 1",
        (name.lower(),),
    )
    return match[0][0] if match else None


def ensure_household_sundries():
    """The one subcategory among the missing rows with no existing Item
    Group match at all -- created once, under "Household" (matching its
    obvious parent by name), rather than left to silently fail per-item."""
    name = "Household Sundries"
    if frappe.db.exists("Item Group", name):
        return name
    parent = resolve_item_group("Household")
    if not parent:
        frappe.throw("No 'Household' Item Group found to attach 'Household Sundries' to.")
    doc = frappe.new_doc("Item Group")
    doc.item_group_name = name
    doc.parent_item_group = parent
    doc.is_group = 0
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created Item Group {doc.name}")
    return doc.name


def _normalize_brand(name):
    return "".join(ch for ch in name.upper() if ch.isalnum())


def ensure_brand(brand_name):
    """Confirmed live 2026-07-27: a plain case-insensitive exact match missed
    11 real duplicates out of the first 11 "new" brands this produced (e.g.
    "YOUNGS" vs "Young's", "INOVATIVE" vs "Innovative", "K&Ns" vs "K&N's") --
    every single one turned out to be a spelling/punctuation variant of an
    already-existing brand, confirmed by the item names themselves (e.g. an
    item branded "PAMOLIVE" sitting next to one branded "PALMOLIVE"). Fixed
    manually that time; this alphanumeric-normalized check catches the same
    class of variant (stripped apostrophes/spaces/case) without silently
    auto-merging on a fuzzy/uncertain match -- an ambiguous near-miss still
    throws, forcing a manual look rather than a guess."""
    name = brand_name.strip()
    if not name or name.upper() == "NULL":
        return None
    existing = frappe.db.get_value("Brand", {"brand": name})
    if existing:
        return existing
    match = frappe.db.sql("select name from tabBrand where lower(brand) = %s limit 1", (name.lower(),))
    if match:
        return match[0][0]

    normalized = _normalize_brand(name)
    close_matches = [
        row.name
        for row in frappe.get_all("Brand", fields=["name"])
        if _normalize_brand(row.name) == normalized
    ]
    if close_matches:
        frappe.throw(
            f"Brand '{name}' looks like a spelling/punctuation variant of existing brand(s) "
            f"{close_matches} -- resolve manually (pick the right one, don't auto-merge) rather "
            "than letting this create a duplicate."
        )

    doc = frappe.new_doc("Brand")
    doc.brand = name
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created Brand {doc.name}")
    return doc.name


def create_item(row):
    item_code = row["ItemCode"].strip()
    if frappe.db.exists("Item", item_code):
        return False

    item_group = resolve_item_group(row["SubCatName"]) or ensure_household_sundries()
    brand = ensure_brand(row["BrandName"])
    fbr_tax_category = row["Fbr_Tax_Category"].strip() or None
    if fbr_tax_category and not frappe.db.exists("FBR Tax Category", fbr_tax_category):
        print(f"WARNING: FBR Tax Category '{fbr_tax_category}' not found for {item_code} -- left blank")
        fbr_tax_category = None

    doc = frappe.new_doc("Item")
    doc.item_code = item_code
    doc.item_name = row["Description"].strip() or item_code
    doc.item_group = item_group
    doc.stock_uom = "Pcs"
    doc.is_stock_item = 1
    if brand:
        doc.brand = brand
    if fbr_tax_category:
        doc.custom_fbr_tax_category = fbr_tax_category

    doc.append("barcodes", {"barcode": item_code})
    ref_code = row["RefCode"].strip()
    if ref_code and ref_code != item_code:
        doc.append("barcodes", {"barcode": ref_code})

    doc.insert(ignore_permissions=True)
    return True


def main():
    missing = find_missing_rows()
    print(f"Rows in file: matched to existing catalog by barcode, {len(missing)} genuinely missing")

    previous_in_patch = frappe.flags.in_patch
    frappe.flags.in_patch = True
    created = 0
    failed = []
    try:
        for row in missing:
            try:
                if create_item(row):
                    created += 1
                frappe.db.commit()
            except Exception as exc:
                frappe.db.rollback()
                failed.append((row["ItemCode"], str(exc)))
                print(f"FAILED {row['ItemCode']} -> {exc}")
    finally:
        frappe.flags.in_patch = previous_in_patch

    print(f"Created {created} new Items, {len(failed)} failures")
    if failed:
        print("FAILURES:", failed[:50])
