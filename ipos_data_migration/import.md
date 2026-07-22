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
- Valuation rate from `CurCost` — **must be reverse-calculated to tax-exclusive first, never used
  as-is.** Fixed 2026-07-16: `CurCost` in the source workbook is tax-inclusive, but both prior
  reference scripts (`import_siezal_items.py`, `import_hsmitems.py`) used it directly as the Stock
  Entry `basic_rate`/`valuation_rate` with no adjustment — this inflated the imported stock's cost
  basis by the embedded tax on every row, which flows straight into COGS (`Stock Ledger Entry`
  valuation) once that stock sells, without a matching inflation on the Sales side (`net_total`
  is tax-exclusive) — this is what caused `siezal`'s reported COGS to run higher than Sales. See
  "Tax-exclusive valuation rate" below for the exact fix and formula; **any new per-site import
  script must include this step, not just copy the old rate-assignment line.**
- Zero valuation must be allowed on stock-entry rows when source `CurCost` is `0`
- **Negative `Onhand` is genuine negative stock, not a sign/export artifact** (confirmed on `hsm`)
  and must be imported too, as a `Material Issue` (`qty = abs(Onhand)`, `basic_rate` = the same
  tax-exclusive rate) — don't just filter it out with `if qty > 0`. Skipping negative rows
  silently understates the
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

#### Opening-stock GL posting (fixed 2026-07-23)

Every `Stock Entry Detail` row this import creates must set `expense_account` explicitly to the
site's **`Temporary Opening`** account (Asset / Balance Sheet, `account_type: "Temporary"` — the
exact account already used by `import_<site>_suppliers.py`'s opening-balance Journal Entries), never
left to default. Left unset, ERPNext falls back to `Company.stock_adjustment_account` — a plain
**Expense / Profit and Loss** account meant for routine stock-count corrections. Crediting that
account with the entire opening-stock value in one lump sum (confirmed live on `siezal`: Rs
47,145,912.77 across 70 `Material Receipt` entries hit `5119 - Stock Adjustment - SSM`) reads as
phantom P&L income for whatever posting period the migration ran in, distorting reported profit for
that period — this was never supposed to be a routine stock adjustment, it's a one-time
opening-balance entry that must bypass P&L entirely, the same way opening vendor balances already do.

Routing both halves of a migration's opening entries through the same `Temporary Opening` suspense
account (rather than one leaking into P&L on its own) means that once **all** opening entries for a
site are posted — items, suppliers, and any others (customer opening balances, cash/bank, etc.) —
`Temporary Opening`'s net balance is exactly the site's opening equity contribution (or drawdown),
by construction. That residual must be closed out via one final Journal Entry to
**`Opening Balance Equity`** (Equity / Balance Sheet, already present in the standard CoA) —
`close_migration_opening_balance.py` (see Reference Scripts below) does this. Run it once, by hand,
after both the item and supplier imports for a new site are fully done — it is idempotent (a no-op
if `Temporary Opening` already nets to zero).

**This was not applied retroactively to `szl`/`siezal`/`hsm`'s already-completed migrations** — their
existing opening-stock entries still credit `Stock Adjustment`, a historical data-quality gap, not
something this fix rewrites. It only changes how the *next* site's import script (copied from
`import_siezal_items.py`) behaves.

#### Branch / Cost Center / Accounting Dimension (fixed 2026-07-23)

Every Stock Entry this script creates must carry the correct `branch` (header + every row) and
`cost_center` (every row) explicitly, resolved once via `get_branch_and_cost_center()` (reverse
lookup through `Warehouse.custom_branch` → `Branch.cost_center`, throwing loudly if either is
unmapped) rather than left for `aimatic.branch_management`'s `apply_branch_defaults` `before_validate`
hook to fill in silently. That hook *does* still fire for these script-inserted Stock Entries and
was confirmed (live, on `siezal`) to already fill in the correct values for every one of the 13,978
Material Receipt rows — but a migration script meant to be copied for a new site should not have an
invisible dependency on that hook's own Administrator/override fallback chain, which can resolve to
the wrong Branch on a company with more than one. Setting these fields explicitly doesn't fight the
hook (`can_override` users only get blanks filled in, never an already-set value overwritten) — it
just makes the script self-contained and fail-fast instead of silently correct-by-luck.

### Tax-exclusive valuation rate (fixed 2026-07-16)

Both reference scripts now reverse-calculate `CurCost` before using it as a Stock Entry rate,
using the **identical formula** `fbr_pos/tax_calculator.py:calculate_fbr_item` already uses for
the sales-side reverse calculation (same rule the app already applies elsewhere, not a new one
invented for this script):

```
sales_tax = inclusive_rate * tax_rate / (100 + tax_rate)
exclusive_rate = inclusive_rate - sales_tax
```

`tax_rate` comes from the item's own `custom_fbr_tax_category` (set from `Fbr_Tax_Category`
during `create_item`, which always runs before stock rows are captured in both scripts — the
category is already committed by the time the rate is read). Exempt items (`tax_rate = 0`) pass
through unchanged, matching the sales-side calculator's own behavior.

**Known caveat, not fully closed**: this only works correctly for rows where `Fbr_Tax_Category`
was actually populated in the source workbook. A row with a blank/invalid category leaves
`Item.custom_fbr_tax_category` unset at this point in the script, so `tax_rate` resolves to `0`
and `CurCost` passes through **unchanged** for that item — same bug as before, just narrowed to
whichever rows have no category. Check the source file's `Fbr_Tax_Category` fill rate before a
new import and treat a high blank-rate as a data-quality issue to fix in the source, not something
this script can compensate for.

**Not retroactively fixed**: this only protects a *future* run of these scripts. `siezal`'s
already-imported stock, and `hsm`'s (imported before this fix existed), still carry the original
tax-inclusive valuation — correcting historical Stock Ledger Entries is a separate, explicit
decision, not something to do as a side effect of fixing the script.

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
- `CurCost` -> not Item Price; use only as Stock Entry valuation rate, and only after reverse-
  calculating it to tax-exclusive (see "Tax-exclusive valuation rate" above) — never the raw value
- `MRP` -> `Item.custom_mrp` fallback chain (source data can have `MRP` stored as literal `0`,
  confirmed on `siezal`: 1,289 of 16,491 rows): use source `MRP` if it is nonzero; else derive
  `rp x 1.18` if `rp` is nonzero; else keep whatever is in the source as-is (`0`/blank) — do **not**
  fall back further to `Slprice` or any other field. On the `siezal` file this recovers 18 rows via
  `rp` and leaves the remaining ~1,271 zero/blank rows exactly as the source has them.

### Stock loading mapping

- `Onhand` -> stock quantity loaded through Stock Entry
- `CurCost` -> valuation rate used in the same Stock Entry, tax-exclusive (see above — reverse-
  calculated using the item's own FBR tax rate, not used raw)

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
   - valuation rate = `CurCost` reverse-calculated to tax-exclusive using the item's own FBR tax
     rate (`sales_tax = CurCost * tax_rate / (100 + tax_rate)`, `rate = CurCost - sales_tax`) —
     never `CurCost` raw
   - allow zero valuation when the resulting rate is `0`
   - `expense_account` set to the site's `Temporary Opening` account on every row — see
     "Opening-stock GL posting" above; never left to default
6. Validate imported rows against `MRP` and optional `rp` logic where needed.
7. After both the item and supplier imports are complete for the site, run
   `close_migration_opening_balance.py` once to close `Temporary Opening`'s residual to
   `Opening Balance Equity`.

## Confirmed Non-Mappings

- Do not import legacy `ItemCode` into ERPNext Item code.
- Do not import `CurCost` into buying price lists.
- Do not import `DISCOUNTPRICE`.
- Do not import `Stock_Value`.
- Do not import old GST columns (`Old_GSTTYPE`, `Old_Sales_GSTPer`, `Old_Purchase_GSTPer`) — the
  tax-exclusive valuation rate fix above deliberately uses the item's *current* `custom_fbr_tax_category`
  rate instead of these legacy per-row percentages, for consistency with how the sales side
  calculates tax everywhere else in the app; don't resurrect these columns as an alternative source
  for the reverse calculation.
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
- `close_migration_opening_balance.py` (2026-07-23) — the cross-cutting final step, shared across
  sites, not item- or supplier-specific: closes the `Temporary Opening` suspense account's residual
  balance to `Opening Balance Equity` once both imports for a site are complete. See "Opening-stock
  GL posting" above for why this exists.
- `update_hsm_item_brands.py` (2026-07-22) — a narrower, standalone follow-up pass, not a full item
  import: sets `Item.brand` on already-imported `hsm` items from a separate AI-generated catalogue
  workbook, `itemmastersto.xlsx` (`Query1` sheet, columns `ItemCode`/`AiBrand`/`AiCompany`/
  `AiDepartment`/`AiCategory`/`AiSubCategory`/`AiCoreCategory`/`AiDescription`/
  `AiShortDescription` — only `ItemCode` and `AiBrand` are consumed by this script; the rest is
  unused category-classification data, out of scope here since it needs a separate Item Group
  decision, see the "Arfa Food" gotcha below). Despite the column name, `ItemCode` in this sheet is
  actually a **barcode**, not an ERPNext `item_code` — matched against `Item Barcode.barcode`
  (`get_barcode_to_items`), not `Item.item_code` directly. Rows are skipped when: `AiBrand` is blank
  or literally `"Generic"` (case-insensitive; the sheet uses `"Generic"` as its placeholder for
  "brand unknown", present on roughly a third of the ~231k rows); `ItemCode` is shorter than 8
  characters (guards against non-barcode junk values in that column); or the barcode has no match in
  `hsm`'s `Item Barcode` table (expected for most rows — this workbook spans a much larger item
  universe than any one site actually stocks). When a barcode matches more than one Item, every
  matched Item is updated, not just one. Missing `Brand` records are created on demand
  (`ensure_brand`); MariaDB's default case-insensitive collation on `Brand.name` means a brand
  string that differs only in case from an already-created Brand reuses that record rather than
  creating a near-duplicate. Live run on `hsm` (2026-07-22): 7,509 write operations across 7,163
  distinct Items (the gap is items matched under more than one barcode in the sheet), 997 new Brand
  records, 156,824 rows skipped for no barcode match, 67,086 skipped as generic/blank brand. A
  `DRY_RUN` flag at the top of the script gates all writes behind a read-only counts-only pass —
  always run with `DRY_RUN = True` first and sanity-check the summary before flipping it off.
  **Known data-quality issue surfaced by this sheet, not yet acted on**: `AiCategory` uses `"Arfa
  Food"` as a value across several unrelated food subcategories (dry fruits, pulses, chickpeas) —
  looks like a supplier/brand name that leaked into what should be a generic category label. Nothing
  in ERPNext (no Item Group) is named this today; it only exists as row data in this workbook. If a
  future pass builds Item Groups from `AiCategory`/`AiSubCategory`/`AiCoreCategory`, `"Arfa Food"`
  needs renaming to a real generic category name first — deliberately deferred, not part of this
  brand-only script.
- `update_siezal_item_brands.py` (2026-07-23) — the SIEZAL-safe version of the separate catalogue
  brand pass. It reads `itemmastersto.xlsx` / `Query1` and joins the sheet's `ItemCode` to
  `Item Barcode.barcode` (the source value is a barcode, not ERPNext `Item.item_code`), then assigns
  `AiBrand`. It skips blank/`Generic` brands, codes shorter than 8 characters, and barcodes absent
  from SIEZAL. It normalizes case/space/punctuation-only brand variants (`LU`/`Lu`, `CandyLand`/
  `Candy Land`, `Mitchell's`/`Mitchells`), collects all votes before
  writing, resolves a genuine multi-brand conflict only when one brand has a strict majority, and
  leaves equal-count ties blank for manual review. It has a hard `TARGET_SITE = "siezal"` guard,
  creates missing Brand masters, commits in batches, and is checked in with `DRY_RUN = True`.
  Pre-write dry run: 231,419 source rows, 7,623 matching rows, 7,358 distinct matched Items, 7,318
  assignable Items, 831 canonical brands, and 40 tied conflicts. The 2026-07-23 live pass created
  831 Brand masters and assigned 7,318 Items; database verification then showed 16,491 Items total,
  7,318 branded, 9,173 blank, and a repeat dry run reported all 7,318 as already correct with no
  pending updates. The 40 ties were intentionally not guessed. Backup taken immediately before the
  write: `sites/siezal/private/backups/20260723_013705-siezal-database.sql.gz` plus its matching
  `20260723_013705-siezal-site_config_backup.json`.
- `assign_hsm_item_groups.py` (2026-07-22) — the deferred Item Group follow-up to the above: builds
  a 2-level Item Group tree (`Department` > `Category`, from `itemmastersto.xlsx`'s `AiDepartment`/
  `AiCategory` columns only — `AiSubCategory`/`AiCoreCategory` are too granular and were deliberately
  not used, a product decision made explicit at the time rather than assumed) and assigns every
  matched `hsm` Item to its resolved leaf Category group. Same barcode-matching approach as the brand
  script (`ItemCode` -> `Item Barcode.barcode`, `>= 8` char guard); the `"Arfa Food"` mislabeling
  (see above) is renamed to `"Dry Foods"` before the tree is built (`CATEGORY_RENAMES`). When an
  item's several matched sheet rows disagree on department/category (50 of 7,258 items, 0.7%), the
  most-frequent `(dept, cat)` pairing wins, ties broken by whichever row appears later in the sheet.
  Live run on `hsm`: 11 departments, 39 categories, 7,258 items assigned (the remaining 4,121 of
  hsm's 11,379 items — not covered by this sheet at all — stay on the pre-existing default `Products`
  group untouched).
  - **Self-named department/category collision gotcha**: two of the 39 pairs have an identical
    department and category string (`'Electronics' > 'Electronics'`, `'Textile' > 'Textile'`).
    `Item Group.name` is globally unique, so once the department node (`is_group=1`) was created, the
    later attempt to create the same-named leaf category silently no-opped (`frappe.db.exists` found
    the department node itself) and those 66 items (44 + 22) ended up with `item_group` pointing at
    the **group** node instead of a leaf, inconsistent with every other item in the tree. Fixed
    post-run by flipping just those two `Item Group` rows from `is_group=1` to `is_group=0` (verified
    first that neither had any child groups) rather than reworking the assignment script — a
    department that only ever contains one same-named category is really just a single leaf, not a
    group with one child. Worth building this self-name check into the script itself if this sheet
    (or one shaped like it) is ever re-run for another site.

**When starting a new site's import, copy the most complete existing script into a new
`import_<site>_items.py` in this same directory** rather than writing from scratch or leaving a
working copy only in a site's private files — and update this doc (and `supplierimport.md` if
relevant) in the same session if the new site's data surfaces a mapping decision not already covered
here.
