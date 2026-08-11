"""One-time repair: merge duplicate Items created 2026-07/08 when a barcode
was typed straight into item_code for a product that already had a proper
STO-ITEM-.YYYY.- item, instead of scanning/searching for the existing item.
Six pairs had a genuine STO-ITEM item already carrying the barcode in its
Item Barcode table; a seventh pair (Shahi Chewra Nimko) had no STO-ITEM item
at all, just a typo'd barcode duplicate.

Run once via: bench --site szl execute
aimatic.ipos_data_migration.merge_duplicate_barcode_items.run

Survivor is always the STO-ITEM series code (or, for the Nimko pair, the
duplicate with real transaction history renamed onto a fresh series code).
frappe.rename_doc(merge=True) reattributes every Link reference (Stock
Ledger Entry, GL Entry, Purchase Receipt Item, Sales Invoice Item, Item
Price, Item Barcode, ...) from the old name to the surviving name and
deletes the old Item doc, so stock/GL history follows the survivor instead
of needing manual reconciliation.
"""

import frappe
from frappe.model.naming import make_autoname

from aimatic.item_naming.events import ITEM_CODE_SERIES

TARGET_SITE = "szl"

MERGE_PAIRS = [
    ("6000123599102", "STO-ITEM-2026-05179"),
    ("7622202871665", "STO-ITEM-2026-16528"),
    ("8001673522235", "STO-ITEM-2026-16529"),
    ("8001673522457", "STO-ITEM-2026-16530"),
    ("8964002345656", "STO-ITEM-2026-13115"),
    ("8992725910202", "STO-ITEM-2026-14145"),
]

# No STO-ITEM item exists yet for this product; the duplicate with real
# transaction history becomes the survivor under a fresh series name, then
# the empty typo duplicate merges into it.
NIMKO_ACTIVE = "8966000002043"
NIMKO_TYPO = "89660000002043"


def run():
    if frappe.local.site != TARGET_SITE:
        frappe.throw(f"This script is locked to {TARGET_SITE}, not {frappe.local.site}.")

    for old_name, survivor in MERGE_PAIRS:
        _merge(old_name, survivor)

    _merge_nimko()

    frappe.db.commit()


def _merge_nimko():
    # Already run: NIMKO_ACTIVE was renamed onto a fresh series code, so
    # there's nothing left to do - re-running must not blow a fresh series
    # number or crash trying to rename a name that no longer exists.
    if not frappe.db.exists("Item", NIMKO_ACTIVE):
        return
    new_name = make_autoname(ITEM_CODE_SERIES)
    frappe.rename_doc("Item", NIMKO_ACTIVE, new_name)
    _ensure_barcode(new_name, NIMKO_ACTIVE)
    _merge(NIMKO_TYPO, new_name)


def _merge(old_name, survivor):
    if not frappe.db.exists("Item", old_name):
        return
    frappe.rename_doc("Item", old_name, survivor, merge=True)
    _ensure_barcode(survivor, old_name)


def _ensure_barcode(survivor, barcode_source_name):
    if not barcode_source_name.isdigit():
        return
    exists = frappe.db.exists(
        "Item Barcode", {"parent": survivor, "barcode": barcode_source_name}
    )
    if exists:
        return
    doc = frappe.get_doc("Item", survivor)
    doc.append("barcodes", {"barcode": barcode_source_name})
    doc.save(ignore_permissions=True)
