import re

import frappe
from frappe.model.naming import make_autoname

# Same series the iPOS migration scripts already used for bulk-imported
# items (see ipos_data_migration/import_*.py) - continuing it here keeps one
# counter instead of starting a second, colliding series.
ITEM_CODE_SERIES = "STO-ITEM-.YYYY.-"

# Item codes typed as a literal barcode (EAN-8/12/13, UPC, etc.) rather than
# picked from the series above.
_BARCODE_LIKE = re.compile(r"^[0-9]{4,18}$")


def autoname_item_code_from_series(doc, method=None):
    """Always assign a new Item's item_code from the STO-ITEM series.

    A typed value is never used as item_code itself - that's what let
    someone create a second Item for a product that already existed, just by
    typing its barcode into item_code instead of finding the existing Item
    (see block_duplicate_barcode_on_create below, and the 7 duplicates
    repaired via ipos_data_migration/merge_duplicate_barcode_items.py).
    Instead, if what was typed looks like a real barcode, it's preserved as
    an Item Barcode row so it isn't lost - it just can no longer become the
    item's identity/name. Runs after core autoname() via the same "autoname"
    doc_event; only fires on creation (autoname is never called on update).
    """
    typed = (doc.item_code or "").strip()
    doc.item_code = make_autoname(ITEM_CODE_SERIES)
    doc.name = doc.item_code

    if typed and _BARCODE_LIKE.match(typed):
        if not any(row.barcode == typed for row in (doc.barcodes or [])):
            doc.append("barcodes", {"barcode": typed})


def block_duplicate_barcode_on_create(doc, method=None):
    """Stop a new Item from being created with a barcode already owned by
    another Item, instead of the user finding and using that existing Item.

    This is exactly how 7 duplicate items ended up in production (2026-07/08,
    repaired via ipos_data_migration/merge_duplicate_barcode_items.py): a
    barcode was typed into item_code for a product that already had a proper
    STO-ITEM-.YYYY.- item carrying that same barcode, silently forking the
    catalog and splitting that product's stock/sales history across two
    records. item_code itself can no longer collide (it always comes from
    the series - see autoname_item_code_from_series), so this checks every
    barcode the new Item is about to be saved with, including one just
    carried over from a typed item_code. Only runs on creation - editing an
    existing item's barcodes is unaffected.
    """
    if not doc.is_new():
        return

    for row in doc.barcodes or []:
        if not row.barcode:
            continue
        owner = frappe.db.get_value("Item Barcode", {"barcode": row.barcode}, "parent")
        if owner:
            frappe.throw(
                f"Barcode {row.barcode} already belongs to Item {owner}. "
                "Use that item instead of creating a new one."
            )


def ensure_item_barcode_row(doc, method=None):
    """Mirror a barcode-shaped item_code into the Item Barcode child table.

    POS resolves a scanned barcode from the Item Barcode table only -
    offline_pos.api._resolve_item_code_from_barcode never falls back to
    item_code - so an item whose barcode was typed straight into item_code
    (instead of, or in addition to, an Item Barcode row) fails to resolve
    server-side even though it looks fine on the Item form. Only acts on
    numeric, barcode-shaped codes (never the auto-generated
    STO-ITEM-YYYY-NNNNN series) and only when that value isn't already
    present as a barcode.
    """
    if not doc.item_code or not _BARCODE_LIKE.match(doc.item_code):
        return
    if any(row.barcode == doc.item_code for row in (doc.barcodes or [])):
        return
    doc.append("barcodes", {"barcode": doc.item_code})


def backfill_missing_barcode_rows():
    """One-time repair for items created before ensure_item_barcode_row
    existed, whose barcode-typed item_code was never mirrored into Item
    Barcode.

    Before autoname_item_code_from_series existed, item_code was reqd with
    no autoname wired up, so a user creating a new item with no real code to
    hand typed the physical barcode straight into item_code instead - it
    never came from (and doesn't match) the STO-ITEM-.YYYY.- series. Target
    exactly that: any non-disabled item whose item_code isn't a series name,
    and that has no matching Item Barcode row yet. Excludes item_code values
    that aren't barcode-shaped at all (e.g. hand-typed SKUs with letters)
    since those were never meant to be scannable.

    Not a patch - invoke manually (`bench execute
    aimatic.item_naming.events.backfill_missing_barcode_rows`) against a
    site only after the usual backup/approval gate, since it writes.
    """
    rows = frappe.db.sql(
        """
        SELECT i.item_code
        FROM `tabItem` i
        WHERE i.item_code REGEXP '^[0-9]{4,18}$'
          AND i.item_code NOT REGEXP '^STO-ITEM-[0-9]{4}-'
          AND i.disabled = 0
          AND NOT EXISTS (
              SELECT 1 FROM `tabItem Barcode` b WHERE b.parent = i.item_code
          )
        """,
        as_dict=True,
    )
    for row in rows:
        doc = frappe.get_doc("Item", row.item_code)
        doc.append("barcodes", {"barcode": row.item_code})
        doc.save(ignore_permissions=True)
    frappe.db.commit()
    return len(rows)
