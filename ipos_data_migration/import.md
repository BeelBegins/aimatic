# Legacy Item Import Mapping To ERPNext

This document defines how old software item data should be mapped into ERPNext.
It is intended to be reusable across `szl`, `siezal`, `hsm`, and later sites using the same legacy file structure.
Current scope: item, barcode, price, FBR category, and stock import only. Supplier import is intentionally deferred to a separate `supplierimport.md`.

## Purpose

- Keep one shared mapping reference in the `aimatic` app instead of site-private files.
- Reuse the same ERPNext field decisions across multiple site migrations.
- Record operational prerequisites discovered during test imports so future runs do not start from scratch.

## Import Principles

- Use standard ERPNext fields only.
- Do not create new custom fields for this import format.
- Ignore `Sheet2`; it contains no useful import data.
- ERPNext owns item-code generation through its own naming series.
- Legacy `ItemCode` is not imported into ERPNext Item `item_code`.
- Item stock UOM is `Pcs`.
- `Pcs` must have `Must be Whole Number` disabled before stock import because source `Onhand` contains decimal quantities.
- `Onhand` and `CurCost` are used to create opening/current stock through Stock Entry.
- `allow_negative_stock` may be enabled during testing/import preparation where needed.
- `Stock Settings.valuation_method` must be `Moving Average` before running the stock-entry step —
  confirm this before any import, don't assume it's already set (the `hsm` site was left on the
  ERPNext default `FIFO` and had to be switched after the fact).
- `allow_zero_valuation_rate` on each Stock Entry row must be set **conditionally**
  (`rate <= 0`), never blanket-`1` for every row. Setting it unconditionally is what let a bad
  import run on `hsm` post 9,629 stock rows at `valuation_rate = 0` — with real, correct `CurCost`
  values sitting right there in the source file — without a single error, because the flag told
  Frappe a zero rate was fine even for rows that should never have had one. The whole point of the
  flag is to only be a safety valve for genuinely zero-cost rows.
- Free-text fields sourced from the legacy file (`Description` -> `item_name`, and `CatName`/
  `SubCatName` -> Item Group names, see below) are normalized to Proper/Title Case on import — the
  legacy system stores everything upper-case (`"BIRTHDAY ITEM FOIL BANNER"`), which is not how these
  should read in ERPNext. Obvious legacy typos are corrected in the same pass rather than imported
  verbatim (e.g. `PACKEGES` -> `Packages`, `ELECTONIC ITEMS` -> `Electronic Items`, both seen on
  `siezal`).

## Relevant ERPNext Fields

### Item

- `item_name`
- `stock_uom`
- `custom_mrp`
- `custom_fbr_tax_category`
- `item_group` — only when the source file carries category columns (`CatName`/`SubCatName`), see
  "Category / Item Group mapping" below. Not every legacy file shape has these columns.
- `is_stock_item`
- `is_purchase_item`
- `is_sales_item`
- `description`
- `valuation_rate`

### Item Price

- Standard Selling price list entry

### Item Barcode

- Two plain barcode rows may be created per item

### Stock Entry

- Quantity from `Onhand`
- Valuation rate from `CurCost`
- Zero valuation must be allowed on stock-entry rows when source `CurCost` is `0`
- **Negative `Onhand` is genuine negative stock, not a sign/export artifact** (confirmed on `hsm`)
  and must be imported too, as a `Material Issue` (`qty = abs(Onhand)`, `basic_rate = CurCost`) —
  don't just filter it out with `if qty > 0`. Skipping negative rows silently understates the
  imported stock value versus the source file's own `SUM(Onhand*CurCost)` (this is exactly how the
  `hsm` gap was first caught: the reported total didn't match a plain Excel `SUMPRODUCT` over the
  whole sheet). Confirm this is genuine negative stock for the specific legacy system before
  assuming the same for a different site/source file.
- A `Material Issue` with no prior stock/valuation history for that item will **not** accept a rate
  from the row itself — ERPNext raises `"Valuation Rate for the Item ... is required"` instead of
  using `basic_rate`, because outgoing rate is normally derived from existing valuation history, and
  a negative-Onhand item by definition has none yet. Set `Item.valuation_rate = CurCost` first (one
  `frappe.db.set_value` per item, committed before the Stock Entry) so the outgoing transaction has
  something to draw its rate from — verified working end-to-end on `hsm` before being written into
  the reference script.

## Legacy Header Mapping

### Item master mapping

- `Description` -> `Item.item_name`
- `MRP` -> `Item.custom_mrp`
- `Fbr_Tax_Category` -> `Item.custom_fbr_tax_category`
- `ItemCode` -> first Item Barcode row
- `RefCode` -> second Item Barcode row

### Supplier mapping

- `SupplierName` -> ignored in the current import scope
- Supplier import will be documented separately in `supplierimport.md`

### Pricing mapping

- `Slprice` -> Item Price in `Standard Selling`
- `CurCost` -> not Item Price; use only as Stock Entry valuation rate
- `MRP` -> `Item.custom_mrp` fallback chain (source data can have `MRP` stored as literal `0`,
  confirmed on `siezal`: 1,289 of 16,491 rows): use source `MRP` if it is nonzero; else derive
  `rp x 1.18` if `rp` is nonzero; else keep whatever is in the source as-is (`0`/blank) — do **not**
  fall back further to `Slprice` or any other field. On the `siezal` file this recovers 18 rows via
  `rp` and leaves the remaining ~1,271 zero/blank rows exactly as the source has them.

### Stock loading mapping

- `Onhand` -> stock quantity loaded through Stock Entry
- `CurCost` -> valuation rate used in the same Stock Entry

### Category / Item Group mapping (only when source file has `CatName`/`SubCatName`)

Some legacy file exports (first seen on `siezal`'s `itemasterghuritown.xlsx`) additionally carry
`catcode`, `CatName`, `ItemOther_SubCategory`, `SubCatName`, `ItemOther_BrandCode`, and `supcode`.
Only `CatName`/`SubCatName` are mapped; the rest are ignored (see "Ignored Legacy Columns").

- `CatName` -> parent Item Group; `SubCatName` -> child Item Group under that parent;
  `Item.item_group` = the leaf (subcategory) Item Group.
- `catcode`/`ItemOther_SubCategory` (the legacy numeric category IDs) are not stored anywhere — no
  new custom "category code" field is created, per the "no new custom fields" principle above. There
  is no ERPNext-native code/numbering concept for Item Group, so the tree is built from names only;
  a brand-new category added later in ERPNext is just a new tree node, nothing to auto-number.
- `ItemOther_BrandCode` / Brand is not mapped — skipped entirely.
- The literal string `"NULL"` appearing as a `CatName`/`SubCatName` value (an export artifact, not a
  real category — seen on `siezal`) is treated as blank/uncategorized, not built into a `Null` Item
  Group node.
- A row that is obviously legacy test data (e.g. `siezal`'s `ItemCode 14`, `Description "TEST3"`,
  `CatName "TEST ITEMS"`, `SubCatName "TESTING"` — a single junk row) is left uncategorized rather
  than building a category around it.
- **Subcategory-parent conflicts**: the same `SubCatName` can appear under more than one `CatName`
  across different rows (a legacy mis-tagging issue — 27 of 89 subcategories on the `siezal` file).
  Resolve by majority vote: whichever parent a subcategory co-occurs with most often (by row count)
  becomes its one true parent in the built tree; minority rows are re-pointed to that same resolved
  parent. Every subcategory has exactly one parent in the final tree.
- **Merging near-duplicate sibling subcategories**: where two subcategory names under the same
  resolved parent clearly mean the same thing, merge them into one node rather than keeping both —
  reviewed manually (string-similarity alone produces mostly false positives, e.g. `FEMALE GROOMING`/
  `MALE GROOMING` or `BANDAGES`/`BANGLES` are NOT duplicates despite scoring high). Worked examples
  from `siezal`:
  - `Household Essentials` (299 items) + `Household Sundries` (241 items), both under `Household` ->
    merged into one `Household Essentials` node (540 items).
  - `Watch` (38 items, was under `Household`) + `Wrist Watches` (52 items, was under `Gent
    Essentials`) -> merged into one `Wrist Watches` node under `Gent Essentials` (90 items) — chosen
    over `Household` because a watch is a personal accessory, not a general household good.
- This category/subcategory mapping is a per-file decision, not a universal rule — a future site's
  file with the same column shape still needs its own likeness review; don't blindly reapply the
  `siezal` merges above to a different site's data.

## Ignored Legacy Columns

- `OB`
- `DISCOUNTPRICE`
- `QTY`
- `Stock_Value`
- `Old_GSTTYPE`
- `Old_Sales_GSTPer`
- `Old_Purchase_GSTPer`
- `Tax_Rate`
- `catcode`, `ItemOther_SubCategory` — legacy numeric category/subcategory IDs; superseded by
  `CatName`/`SubCatName` for the Item Group tree, not stored themselves (first seen on `siezal`)
- `ItemOther_BrandCode` — Brand is not mapped in this import (first seen on `siezal`)
- `supcode` — matches the supplier file's `SupplierCode` for most rows on `siezal`, but is still not
  used to auto-link a default Supplier onto the Item, per the existing "Confirmed Non-Mappings" rule
- `Has_Opening_Balance`, `Exists_In_SaleDetail`, `Exists_In_DebitDetail`,
  `Exists_In_AdjustmentCreditDetail`, `Exists_In_AdjustmentDebitDetail`, `Exists_In_CreditDetail` —
  legacy bookkeeping audit flags with no ERPNext target (first seen on `siezal`)

## Column Meaning Notes

- `ItemCode` = first barcode
- `RefCode` = second barcode
- Both are treated only as plain item barcodes
- No barcode is treated specially as a box barcode or UOM barcode
- `QTY` is not used for UOM conversion
- `Tax_Rate` is ignored if `Fbr_Tax_Category` is present
- `rp` means pre-tax retail price where `rp + 18% = mrp` when `rp` exists

## Barcode And UOM Rule

For this import format:

- Do not differentiate unit barcode vs box barcode
- Do not create UOM conversions from the legacy file
- Do not use `QTY` to create Box/UOM relationships
- Keep the ERPNext stock UOM as `Pcs`
- Import stock directly in `Pcs`

## Recommended Import Sequence

1. Create Items with ERPNext naming series.
2. Add up to two barcodes per item from `ItemCode` and `RefCode`.
3. Set item-level FBR Tax Category from `Fbr_Tax_Category`.
4. Create `Standard Selling` Item Price from `Slprice`.
5. Create stock through Stock Entry using:
   - quantity = `Onhand`
   - valuation rate = `CurCost`
   - allow zero valuation when `CurCost = 0`
6. Validate imported rows against `MRP` and optional `rp` logic where needed.

## Confirmed Non-Mappings

- Do not import legacy `ItemCode` into ERPNext Item code.
- Do not import `CurCost` into buying price lists.
- Do not import `DISCOUNTPRICE`.
- Do not import `Stock_Value`.
- Do not import old GST columns.
- Do not derive any UOM structure from `QTY`.
- Do not auto-link Supplier as Item default supplier.
- Do not import Supplier data in this phase.

## Final Decisions

- `rp` is not stored in ERPNext. It is reference-only for validation against `MRP` where needed.
- Supplier import is out of scope for this document and will be handled later in a separate `supplierimport.md`.

## Backup Requirement For Test Imports

Before test imports, create a restore-capable site backup.
The exact backup path is site-specific and should be recorded in the migration notes for that run.

## Site-Specific Import Targets

- `siezal` (`itemasterghuritown.xlsx`, Ghouri Town): Company/Branch/Cost Center/Warehouse target is
  the site's one real branch, `Ghouri Town Phase V`; posting date is the run date, set explicitly at
  run time rather than left to default (per the existing `hsm` script convention).

## Reference Scripts

Every per-site import script lives in `apps/aimatic/ipos_data_migration/` (this directory) alongside
this doc — not in any site's `private/files/` — so the whole migration toolkit stays one browsable,
git-tracked unit. A script's `FILE_PATH` constant still points at wherever the source workbook was
actually uploaded on that site; only the script itself is centralized.

- `import_hsmitems.py` — original/simplest reference implementation, dependency-free XLSX parsing
  (raw `zipfile`/`ElementTree`, no `openpyxl`).
- `import_siezal_items.py` — the more complete reference: adds the Category/Item Group tree
  (majority-vote parent resolution, manual near-duplicate merges, Proper Case + typo fixes,
  `"NULL"`/junk-row handling, spreadsheet footer-row filtering) and the `MRP` fallback chain
  documented above, all first introduced for `siezal`. Prefer this one as the starting point for a
  new site's script.

**When starting a new site's import, copy the most complete existing script into a new
`import_<site>_items.py` in this same directory** rather than writing from scratch or leaving a
working copy only in a site's private files — and update this doc (and `supplierimport.md` if
relevant) in the same session if the new site's data surfaces a mapping decision not already covered
here.
