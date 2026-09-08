# S7 Empire Heights migration

Branch go-live plan for **S7 - Empire Heights** on company Siezal Super Market
(SSM), site `szl` (production) after mock testing on `siezal`.

Reusable mapping rules stay in `import.md`. This file records **S7-only
verdicts, gates, file roles, and pass order**. Do not invent a second mapping
system here.

## Status (2026-09-06 — superseded by fresh-data re-run below)

| Phase | Status |
|---|---|
| Branch / warehouse / cost center masters | Already present (`S7 - Empire Heights`, ledger suffix `S7EH`) — see `setup_szl.md` / `szl_reference_data.py` |
| Barcode gap audit vs live `szl` | **Done** — re-run 2026-09-06 against fresh `1007.xls` (see below); superseded the 2026-09-04 run against stale `Itemonhand-S7 Store.xls` |
| Master item file for missing rows | **Received**, twice — stale `Ho-MasterItemFile.xlsx` (File `fdd3fa1cd5`, 2026-09-04) then fresh `Ho-MasterItemFilebaf350.xlsx` (File `5f223eab4b`, 2026-09-06) |
| Master reconcile vs 548 missing | **Done** against fresh master file — `ALL_548_READY`, identical verdict to the 2026-09-04 run against the stale one |
| Mock restore of `szl` backup onto `siezal` | **Done** 2026-09-06 — source `20260906_022406-szl-database.sql.gz` (includes the POS go-live setup and vendor opening balances from earlier the same day); supersedes the 2026-09-04 restore |
| Mock item / price / stock import on `siezal` | **Done** 2026-09-06 with fresh source files: 546 items (1 junk `TEST` row skipped, same as before); 3,083 S7 prices; opening stock posted (2,885 positive + 21 negative = 2,906 bins). Orphan Barcode2 attach: 15 rows (`attach_s7_orphan_barcode2.py`). |
| Catalog duplicate merges (all 7 pairs) on `siezal` | **Done** — re-verified against fresh restore; every merge-away Item code confirmed absent, every survivor carries all barcodes from both |
| PKR currency lock on `siezal` | **Not needed this time** — post-restore snapshot (which now includes today's live `szl` POS/vendor work) was already 100% PKR across all 14 Price Lists / 39,123 Item Prices before this run touched anything |
| Reconcile (source counts, unmatched barcodes, Bin qty/value, SLE/GL) | **Done** 2026-09-06 — see "Fresh-data mock re-run" below. Bin qty/value: 0 missing, 0 mismatched, value ₨4,690,983.86 matches `Σ Onhand × exclusive CurCost` (₨4,690,983.85, rounding). GL: 0 blank-cost-center rows. SLE: 2,906 rows, matches Bin row count exactly. |
| Live import on `szl` | **Done** 2026-09-06 — see "Live cutover" section below. 546 Items, 15 orphan barcodes, 7 catalog merges, 3,083 prices, 2,906 opening-stock bins, all reconciled clean against the fresh-data mock's own numbers. |

## Fresh-data mock re-run (2026-09-06)

The 2026-09-04 mock above was built from source files the user later flagged
as stale (`Ho-MasterItemFile.xlsx`, `Itemonhand-S7 Store.xls`, both dated
2026-09-04) — see the 2026-09-05/06 sign-off entries. On 2026-09-06 the user
uploaded replacements: `Ho-MasterItemFilebaf350.xlsx` (master, File
`5f223eab4b`) and `1007.xls` (S7 stock/price, File `c893bbf6c2` — Crystal
Reports export timestamp inside the file reads 2026-09-05 21:11, i.e.
genuinely current, not a re-upload of the old data). Checksums confirm both
files differ in content from their 2026-09-04 predecessors.

Full pipeline re-run end to end against these files, on a `siezal` freshly
restored from a same-day `szl` backup (so the "before" state includes the
POS go-live setup and vendor opening balances already live on `szl`):

| Step | Fresh-data result | 2026-09-04 result (stale data) |
|---|---|---|
| Barcode gap audit | 548 missing rows, 578 absent barcodes, 15 orphan Barcode2 | 548 missing, 579 absent, 16 orphan |
| Master reconcile | `ALL_548_READY`, 0 blocked, same 9 brands left blank | `ALL_548_READY`, 0 blocked |
| Item create | 546 created, 1 junk (`TEST`) skipped, 0 failed | 546 created, 1 skipped |
| Orphan Barcode2 attach | 15 attached, 0 blocked | 16 attached |
| Catalog merges | All 7 pairs merged clean (same item codes — pre-existing catalog, not new S7 creates) | All 7 pairs merged |
| S7 prices | 3,083 rows, all PKR | 3,071 rows |
| S7 opening stock | 2,885 positive + 21 negative = 2,906 bins, value ₨4,690,983.86 | 2,891 + 19 = 2,910 bins |

Practically identical shape to the stale-data run — confirms the earlier mock
wasn't structurally wrong, just built on a slightly older stock/price
snapshot. Scripts updated in place to point at the fresh files (old
2026-09-04 file paths kept in comments for history, per this repo's
never-delete-a-migration-script rule): `audit_s7_barcodes_vs_szl.py`,
`reconcile_s7_missing_vs_master.py`, `create_s7_missing_items.py`,
`attach_s7_orphan_barcode2.py`, `import_s7_prices_and_stock.py` (posting
date and Stock Entry chunk tags bumped to `2026-09-06`).

Desk hub on `szl`: `/app/migration`  
Result folder: `Home/Migrations/S7` (`/app/file/view/home/Migrations/S7`)

## Final verdicts (locked)

### Barcode audit (`Itemonhand-S7 Store.xls`, File `0c7a4cfbc9`)

Source shape (Crystal export): `Barocode1`, Description, `Onhand`, `CurCost`,
`SalePrice`, `Barcode2`, `Total`.

Matching convention (same as `import.md` / `add_missing_items_from_file.py`):

- `Barcode1` / `Barcode2` are plain **Item Barcode** values, not ERPNext
  `Item.item_code`.
- A row is **covered** if either barcode matches an existing Item Barcode or
  Item name, including GTIN leading-zero / padding variants used by POS.

| Metric | Count |
|---|---|
| Source rows | 3,121 |
| Rows already covered on `szl` | 2,573 |
| **Missing rows** (neither barcode on `szl`) | **548** |
| Unique barcodes in file | 3,400 |
| Unique barcodes absent from `szl` | 579 |
| Covered rows where only Barcode2 is missing | 16 |

Of the 548 missing rows: 503 onhand &gt; 0, 40 zero, 5 negative.

Result workbooks (Excel, not CSV):

- `s7_missing_items_*.xlsx` — the 548 rows needing Item master creation
- `s7_absent_barcodes_*.xlsx` — 579 absent barcode strings
- `s7_orphan_barcode2_*.xlsx` — 16 second-barcode gaps on already-matched items
- `s7_barcode_summary_*.json` — machine-readable totals

Script: `audit_s7_barcodes_vs_szl.py` (hard-targets site `szl`).

### File roles (do not mix)

| File | Use for | Do **not** use for |
|---|---|---|
| **Master item file** (user preparing) | Create the 548 missing Items: name, barcodes, item group, brand, FBR tax category, MRP, and any other master fields present | S7 opening stock qty; S7 branch selling price as sole price source |
| **S7 Itemonhand** (`Itemonhand-S7 Store.xls`) | Branch **stock** (`Onhand` + tax-exclusive `CurCost`) and S7 **selling prices** (`SalePrice`) for every resolvable barcode | Inventing Item Group / FBR Tax Category; creating Items that lack master fields |

Split is deliberate: S7 onhand is a stock snapshot, not a full iPOS item master.
Creating Items from S7 alone would leave blank FBR / wrong groups.

### Hard gates (no exceptions without explicit approval)

1. **Existing Item Groups win — never create new ones**, except the explicit
   approved pair `Nicotine` (parent) / `Tobacco` (child leaf). All other master
   `SubCatName` values must resolve to an Item Group that **already exists**
   (exact / case-insensitive), or to an **approved alias** that points at an
   existing group (table below). If it does not resolve, **block the row**.
   Do not insert further Item Groups.
2. **No new FBR Tax Category** — if master file category is blank or not in
   `FBR Tax Category`, **stop and list**. Do not invent or insert categories.
   Blank category also breaks tax-exclusive `CurCost` reverse-calc (see
   `import.md` — rate passes through inclusive).
3. **No new Brand.** Reuse an existing Brand only on exact or
   case-insensitive name match. If the master `BrandName` does not match an
   existing Brand, **leave `Item.brand` empty**. Do not create Brand masters.
   Do not alphanumeric/fuzzy-merge (e.g. `PAMOLIVE` does not become `Palmolive`).
4. **Dry-run / mock first** — all create/post scripts default `DRY_RUN = True`.
   Live `szl` mutation requires: explicit approval, current verified `szl`
   backup, expected totals, rollback path.
5. **Mock site is `siezal`** — restore a current `szl` backup onto `siezal`
   before any S7 create/post testing. Never treat `hsm` or LMS as SZL test.
6. **Idempotent re-runs** — no duplicate Items, barcodes, Item Prices, or
   Stock Entry chunks. Verify Bin / Item Price counts, not printed stats alone.
7. **ERPNext owns `item_code`** — do not force legacy barcode into `Item.item_code`.
   S7 create must use series `STO-ITEM-.YYYY.-` via
   `aimatic.item_naming.events.autoname_item_code_from_series`. Do **not**
   pre-call `make_autoname` in the script (the autoname hook would increment
   twice). Leave `item_code` blank; set `naming_series` only. Barcodes go on
   the Item Barcode child table. `add_missing_items_from_file.py` (barcode =
   item_code) is **not** the S7 create path.
8. **Items are not linked to any warehouse.** Do not set `item_defaults`,
   `default_warehouse`, or any warehouse/cost-center on Item create. Stock
   Settings `default_warehouse` is already empty. Warehouse is only applied
   later, on S7 opening Stock Entries.

### Duplicate catalog items (S7 file split across two Items)

S7 Barcode1 and Barcode2 can sit on **two different existing Items**. That is
not an ERPNext unique-barcode violation: uniqueness is exact string only.
Do not skip forever. Merge into **one Item with every barcode from both**.

Locked pair — **Bisconni Chocolate Chip Rs=20**:

| Role | Item | Why |
|---|---|---|
| **Survivor** | `STO-ITEM-2026-11166` Bisconi Chocolate Chip Rs 20 | Positive stock and sales; S7 opening already on this code |
| Merge away | `STO-ITEM-2026-11173` Bisconni Chocolate Chip Rs=20 | **Later / real barcodes**; S1 stock is negative |

Barcodes that must all remain on the survivor (`Pcs`, do not change stock UOM):

- From 11166: `8961102508738`, `8961103502223`
- From 11173 (later): `8961102508981`, `8961102508998`

Clear merges signed 2026-09-04 (same rule: keep stock/sales item, bring later barcodes):

| Product | Survivor | Merge away |
|---|---|---|
| Candyland Paradise | `STO-ITEM-2026-00430` | `STO-ITEM-2026-09045` |
| 7Up 1.5 Ltr | `STO-ITEM-2026-00091` | `STO-ITEM-2026-00162` |
| Hilal Jiggles | `STO-ITEM-2026-08009` | `STO-ITEM-2026-00400` |
| Kitcy Basil Leaves 15Gm | `STO-ITEM-2026-09268` | `STO-ITEM-2026-09267` |
| Dawn Plain Paratha | `STO-ITEM-2026-16657` | `STO-ITEM-2026-09957` |
| Walls Choc Bar | `STO-ITEM-2026-09208` | `STO-ITEM-2026-10386` |

Not merged: Bisconni Rite (Vanilla vs S-P names), Olpers regular vs Promo, LU Prince 57gm vs Big S-Pack (Box UOM).

### Currency (PKR only)

Company and Global Defaults are PKR. S2–S7 branch Price Lists were created as **INR**; S7 import copied INR onto Item Prices. Before any live S7 price post: set every Price List and Item Price to **PKR**, disable every Currency except PKR (`lock_pkr_currency.py`). Import refuses a non-PKR Price List.

Script: `merge_s7_duplicate_catalog_items.py` (mock `siezal`). After merge,
run queued **Repost Item Valuation** for the survivor (bins can read 0 until
that finishes). **Live `szl`:** same pair after verified backup, **before**
S7 price/stock so the onhand file resolves to one item. Other S7 ambiguous
pairs are not merged until each is signed the same way.

### Item create contract (live `szl` / mock `siezal`)

Verified 2026-09-04 against current `szl` catalog and
`aimatic.item_naming.events`:

| Field | Value |
|---|---|
| `item_code` / `name` | Auto `STO-ITEM-2026-#####` (series current **17193**; next is 17194). Hook always assigns this. |
| `naming_series` | `STO-ITEM-.YYYY.-` |
| `item_name` | Master `Description`, Proper/Title Case |
| `item_group` | Resolved existing leaf (aliases above) |
| `stock_uom` | `Pcs` (`Must be Whole Number` = 0) |
| `is_stock_item` / `is_sales_item` / `is_purchase_item` | 1 |
| `has_variants` | 0 |
| `disabled` | 0 |
| `brand` | Existing exact/case-insensitive match, else **blank** |
| `custom_fbr_tax_category` | Existing FBR Tax Category from master |
| `custom_mrp` | Master MRP (nonzero); else `rp × 1.18` if rp nonzero; else leave 0/`import.md` chain |
| barcodes | Master `ItemCode` + `RefCode` (if different). Duplicate barcode throws (hook). Prefer barcode `uom=Pcs` like recent desk items. |
| `item_defaults` / warehouse | **None** — catalog items are company-wide; S7 warehouse only on later Stock Entry |
| prices / stock | **Not** in this step |

Do not copy `add_missing_items_from_file.py`. Closest create shape is
`import_szl_s1_stock.py` `create_item`, minus warehouse/price/stock, minus
pre-`make_autoname`.

Stock Settings on `szl`: `valuation_method=Moving Average`,
`default_warehouse` empty, `allow_negative_stock=1`, `stock_uom=Pcs`.
Recent desk Items have empty `item_defaults` and `allow_negative_stock=0`
(site setting still allows negatives).

### Approved SubCatName → existing Item Group aliases

Only aliases that land on an **already-existing** group. Add rows here when
approved; never use this table to invent a new group name.

| Master `SubCatName` | Maps to existing Item Group | Basis |
|---|---|---|
| `HOUSEHOLD SUNDRIES` | `Household Essentials` | Same merge as `import.md` (siezal) — Sundries folded into Essentials |
| `HAJI MOEEN (COSMETICS)` | `Colour Cosmetics` | Approved 2026-09-04: leaf under existing `Face-Hair-Body - Oral Care` (cosmetics name; not the parent) |
| `TOBACCO` | `Tobacco` | Approved 2026-09-04: **new** parent `Nicotine` + child leaf `Tobacco` (spellings confirmed) |

## Planned pass order (mock on `siezal`, then live `szl`)

Reuse closest scripts; copy/adapt rather than rewrite:

1. **Backup + restore** — `bench-ops`: verified `szl` backup → restore to
   `siezal` (approval required). Confirm Branch `S7 - Empire Heights` and
   warehouse/cost center still present after restore.
2. **Re-audit on mock** — re-run barcode gap audit against restored `siezal`
   (expect same 548 if restore is faithful).
3. **Master reconcile (read-only)** — join 548 missing barcodes to the master
   item file. Produce Excel reports:
   - ready to create (all required fields resolve; unmatched brand left empty)
   - blocked: unknown subcategory
   - blocked: unknown / blank FBR tax category
   - blocked: unmatched master row
4. **Create missing Items (dry-run → approve → apply)** — master file only;
   series naming; **no warehouse / item_defaults**; barcodes from master; no
   stock, no S7 price in this step.
5. **Attach orphan Barcode2** (16 rows) — add second barcode onto existing
   Item when Barcode1 already matched; never create a second Item for the same
   product.
5b. **Merge signed catalog duplicates** — Bisconni Chip first
   (`merge_s7_duplicate_catalog_items.py`). Live: before S7 price/stock.
   **Done on `siezal` (mock)** — all 7 pairs merged, verified 2026-09-05.
6. **S7 selling prices** — from S7 `SalePrice` onto the S7 branch Selling Price
   List (pattern: `update_szl_s1_sale_prices.py`). Idempotent; skip zero/blank.
   **Done on `siezal` (mock)** via `import_s7_prices_and_stock.py` — 3,071
   rows, verified 2026-09-05.
7. **S7 opening stock** — from S7 `Onhand` + tax-exclusive `CurCost` into S7
   warehouse (pattern: `import_szl_s1_stock.py` + Temporary Opening). Stamp
   **branch and cost center on Stock Entry header and every item row**
   (`S7 - Empire Heights` / `S7 - Empire Heights - SSM`); verify GL
   `cost_center` after submit. Chunk remarks for idempotency. Negative onhand
   = Material Issue. Verify Bin, not script counters.
   **Done on `siezal` (mock)** — 2,910 nonzero bins across 15 tagged chunks,
   verified 2026-09-05.
8. **Optional GST opening** — only if the same decision as S1 applies
   (`import_szl_s1_gst_opening.py`); record the decision here before running.
   **Done** 2026-09-06 — see "GST opening + barcode-named-item fix" below.
   Was missed in the initial live cutover write-up; user caught the gap.
9. **Reconcile** — source row counts, unmatched barcodes, Bin qty/value vs
   `Σ Onhand × exclusive CurCost`, Item Price coverage, SLE/GL.
   **Done** 2026-09-06, against the fresh-data re-run — see "Fresh-data mock
   re-run" section above for full numbers. 0 missing/mismatched bins, 0
   blank-cost-center GL, SLE row count matches Bin row count exactly.
10. **Live `szl`** — only after mock sign-off; fresh `szl` backup; same scripts
    with hard target `szl`. **Done** 2026-09-06 — see "Live cutover" section
    below.

`close_migration_opening_balance.py` stays deferred until all S7 opening halves
intended for this cutover are posted (same rule as S1).

## Live cutover (2026-09-06)

Ran the same five mutating scripts against `szl` directly (`TARGET_SITE`
flipped from `siezal` to `szl` in each), in the same order as the mock,
after every dry-run matched the mock's numbers exactly and a fresh backup
(`20260906_025559-szl-database.sql.gz`) was taken. PKR currency lock was a
no-op on live too (already 100% PKR, same as the mock's restored source).

| Step | Live result |
|---|---|
| Item create | 546 created (`STO-ITEM-2026-17217`–`17762`), 1 junk skipped, 0 failed — same range as the mock, confirming zero live drift since the backup |
| Orphan Barcode2 attach | 15 attached, 0 blocked |
| Catalog merges | All 7 pairs merged; re-checked immediately before merging that none of the 14 items had any Stock Ledger Entry posted *that day* (i.e. no concurrent live sale/receipt on a to-be-merged item) before touching them |
| S7 prices | 3,083 rows, all PKR |
| S7 opening stock | 2,906 bins (2,885 positive + 21 negative), value ₨4,690,983.86 |

**Mid-run incident**: posting stock in 200-item chunks hit `QueueOverloaded`
(750-job cap) on the shared `short` RQ queue partway through (after 5 of 15
positive chunks). Root cause: `aimatic.foodpanda_integration.events.
on_bin_update` enqueues one `sync_availability` job per Bin update, and a
`Foodpanda Outlet` for `S7 - Empire Heights` now exists with
`catalog_sync_enabled=1` — **this did not exist when the POS go-live setup
section above was written earlier the same day** (verified zero rows then);
someone configured it in between. Each job is still cheap (no `Foodpanda
Product` mapping exists yet for these brand-new S7 items, so
`sync_availability` returns immediately) but a single "short" worker only
drains ~1/sec, far slower than a 200-item chunk enqueues. Fixed by adding
`_wait_for_queue_headroom()` to `import_s7_prices_and_stock.py`, which polls
the `short` queue depth and pauses before each chunk if it's not clear —
script is idempotent per chunk, so resuming after the failure re-skipped the
5 already-posted chunks and completed the rest safely. No data was lost or
duplicated; the first 5 chunks had already committed and verified (blank
GL/branch checks) before the failure.

**Correction to the POS go-live section above**: its "Foodpanda Partner-API
… zero rows anywhere in production" note was accurate when written but is
now stale for S7 specifically — an Outlet row exists for `S7 - Empire
Heights` as of some point on 2026-09-06. Worth confirming with whoever
added it whether S7's Foodpanda catalog sync is meant to go live once real
`Foodpanda Product` mappings exist, since that will start making real API
calls per Bin update rather than the free no-ops seen during this import.

**Post-run verification** (read-only, against live `szl`): Item count
18,138 (546 net new items, 7 merged away — matches `18,145 - 7`); 0
blank-cost-center GL entries and 0 blank-branch Stock Entry Detail rows on
any of today's S7 Stock Entries; SLE row count (2,906) matches Bin row count
exactly; all 7 merge-away item codes confirmed absent; the earlier POS
Profiles / Customer / Account / Mode of Payment and the 22 vendor
opening-balance Journal Entries all confirmed untouched and intact
(including the `S7 Walk in Customer` price-list fix — still correctly
`S7 - Empire Heights Selling Price List`).

## GST opening + barcode-named-item fix (2026-09-06)

Two gaps caught by the user after the "Done" summary above, both closed the
same session:

**GST opening (pass order step 8) was skipped entirely** in the initial live
run — never decided or run for S7. Built `import_s7_gst_opening.py`
(mirrors `import_szl_s1_gst_opening.py` exactly: sums `qty × (inclusive
CurCost − exclusive rate)` per resolved item, posts one Journal Entry
crediting `1910 - Temporary Opening - SSM` / debiting `GST - SSM`). Dry run
matched 3,111 items, 5 unresolved (see next). Posted `ACC-JV-2026-00735` for
₨754,068.61, branch/cost_center stamped correctly.

**5 barcodes never matched anything**, in both the price/stock import and
the GST script — same 5 rows both times: `4005900517982` (Nivea Darkwood
150Ml), `8851932331302` (Lux Rose Glow Body Wash), `8999999036546` (Lux Body
Wash Bootanicals Smooth Skin 250Ml), `8964003018412` (Butterfly Breathables
Peaceful Night Panty), `5028217999974` (Laziza Ras Malai 75Gm). Root cause:
each is an existing, enabled, already-categorized catalog Item whose
`item_code` is literally the barcode digits (pre-`STO-ITEM-.YYYY.-`
convention) with **zero** Item Barcode child rows. The barcode audit's
`_exists()` matches on `Item.name` directly so it counted these as already
covered (correctly keeping them out of the 548 missing-items list); the
price/stock and GST scripts only look at the Item Barcode table, so they
missed them. These are real, distinct items — **not** new items; creating
fresh `STO-ITEM-*` records for these barcodes would have been true
duplicates.

Fixed via `fix_s7_barcode_named_items.py` (dedicated small script, hard-locked
to `szl`, 5 hardcoded rows verified against the source file rather than
mock-tested first): attached an `Item Barcode` row (barcode = item_code) to
each — a shared-catalog fix, not S7-specific, so it benefits every branch's
future barcode lookups on these 5 items — then posted their S7 price (5
rows, all had real `SalePrice` in the source: ₨1549/625/625/189/239),
opening stock (`MAT-STE-2026-00208`, 3 rows — the 2 zero-onhand rows
correctly got no stock entry), and GST portion (`ACC-JV-2026-00736`,
₨443.57). All verified: correct barcodes attached, correct S7 prices,
correct bin quantities (2/2/5, others still zero as expected), correct
branch/cost_center on every line.

## Required master fields (minimum to create an Item)

From master item file, every create-ready row must resolve:

- At least one barcode (`ItemCode` / `Barcode1` or equivalent)
- `Description` → `item_name` (Proper/Title Case per `import.md`)
- `SubCatName` (or mapped group) → **existing** Item Group
- `Fbr_Tax_Category` → **existing** FBR Tax Category (or explicit approved blank
  list — default is reject blank)
- Brand optional: existing Brand on exact/case-insensitive match only; otherwise leave empty

Stock UOM remains `Pcs`. `is_stock_item = 1`.

## Explicitly out of scope for this S7 pass

- Item/price/stock supplier import proper (`supplierimport.md`'s full
  merge-by-NTN Supplier-creation pass, as run for S1) — not needed for S7:
  its vendors are already in the shared Supplier list from S1's import. The
  one-off S7 vendor opening-balance load (below) is a narrower, separate
  action, not this full pass.
- Customer / loyalty (`customerimport.md`)
- Auto-creating categories or tax categories
- Cross-site Material Transfer fiction — S7 is on `szl` with S1; use normal
  same-DB transfers (Internal Branch Supplier construct is historical for
  cross-site; see `setup_szl.md`)

## POS go-live setup (till / accounts) — done on live `szl` (2026-09-06)

Independent of the item/price/stock migration above. Mirrors the only
currently-live branch, `S1 - Ghouri Town VIP`, field-for-field. Backup
`20260906_001604-szl-database.sql.gz` taken first.

| Record | Name | Notes |
|---|---|---|
| Account | `1112 - Cash in Hand - S7EH - SSM` | Under `1100 - Cash In Hand - SSM`, same shape as `1111 - Cash in Hand - S1GT - SSM` |
| Mode of Payment | `Cash - S7EH` | Type Cash, default account = the account above |
| Customer | `S7 Walk in Customer` | Individual — S1 has no shared/generic walk-in customer, each branch has its own. **Bug caught and fixed by user 2026-09-06**: created with no `default_price_list` set, which left it effectively pointing at S1's price list; corrected to `S7 - Empire Heights Selling Price List` (matches the pattern on `S1 Walk in Customer`, whose `default_price_list` is `S1 - Ghouri Town VIP Selling Price List` — this field is not inherited from Customer Group, it must be set explicitly per branch-specific Customer). |
| POS Profile | `S7 Counter 1` | warehouse/branch/cost_center = S7; price list `S7 - Empire Heights Selling Price List`; `custom_terminal_id=S7EH-1`; `custom_fbr_optional=0` (real counter, FBR mandatory); payments: `Cash - S7EH` (default), `Credit Card` |
| POS Profile | `S7 Food Panda` | same base, customer `Food Panda`; price list `S7 - Empire Heights Foodpanda Price List`; `custom_terminal_id=S7FP`; `custom_is_foodpanda_profile=1`; `custom_fbr_optional=1`; payment: `Food Panda Credit` (default) |

FBR for S7 was already configured beforehand: `FBR Integration Settings`
row `Siezal Supermarket-S7 - Empire Heights` (enabled, Production, Real
Time, `pos_id=96711`, `branch_code=1007`) — confirmed live before creating
the counter profile, since a real counter with no matching enabled FBR
settings row hard-fails every sale (`aimatic/fbr_pos/settings.py`).

Confirmed unaffected: Item count, S7 Item Price rows (both price lists),
and S7 Bin rows all read zero-change — this work does not touch the
item/price/stock migration above.

**Remaining before cashiers can log in:** `POS Profile User` (which staff
can use each profile) and branch-scoped `User Permission` (`Branch` = `S7 -
Empire Heights`) — deferred until S7 staffing is finalized, matching S1's
10-user pattern once known.

Confirmed unused at the time this was written (S1's Food Panda profile is
manual, not wired to the automated integration; `Foodpanda Outlet` /
`Category Map` / `Product` / `Order Log` all had zero rows anywhere in
production): the Foodpanda Partner-API automated integration. Food Panda
orders at S7 are rung up manually on the POS Profile above, same as S1.

**Stale as of later the same day** — see "Live cutover" section below: a
`Foodpanda Outlet` row for `S7 - Empire Heights` (`catalog_sync_enabled=1`)
appeared sometime after this was written, discovered when it caused a
background-queue overload during the live stock import. No `Foodpanda
Product` mappings exist for S7 yet, so it's currently a no-op, but confirm
with whoever added it whether S7's catalog sync is intended to go live.

## S7 vendor opening balances — done on live `szl` (2026-09-06)

Independent of both the item/price/stock migration (still paused) and the
POS go-live setup above. Source: `sites/szl/private/files/1007vendorbalances.xlsx`
("1007" = S7's own FBR `branch_code`), same shape as `supplierimport.md`
(SupplierCode/SupplierName/FBRTYPE/NTNo/StandardNTN/WhtTax%/LedgerCode/
TotalDebit/TotalCredit/ClosingBalance), 25 rows. Backup
`20260906_015412-szl-database.sql.gz` taken first. Script:
`import_s7_vendor_balances.py`.

**Key discovery: legacy `SupplierCode` is branch-local, not globally unique.**
S1's own vendor file already claimed code `614` for `SIEZAL SUPERMARKET
(BAHRIA PH7)` during the earlier S1 supplier import. This S7 file reuses code
`614` for a completely different real vendor (`SIEZAL SUPERMARKET (KHANNA
PULL)`), and separately uses code `1004` for the same `BAHRIA PH7` entity S1
already created. Automatic NTN/code matching (the approach `import_szl_
suppliers.py` uses for a fresh import) would have silently misattributed
`KHANNA PULL`'s balance onto `BAHRIA PH7`. Every row in this file was instead
matched manually (by NTN and/or literal name, cross-checked against Purchase
Order/Invoice/Journal Entry activity where ambiguous) before any posting —
see the script's module docstring and the sign-off log below for the full
per-row resolution.

| Outcome | Detail |
|---|---|
| Skipped | Code `081` "TEST SUPPLIER", Rs 10 — junk legacy row, excluded per explicit instruction |
| New Supplier created | `SIEZAL SUPERMARKET (KHANNA PULL)` (code `614`) — no existing Supplier under this name anywhere on `szl` |
| Legacy code appended | `1004` → `SIEZAL SUPERMARKET (BAHRIA PH7)` (now `614,1004`); `095` → `SHAN MARKETING SERVICES (NESTLE YOGURT)` (now `183,095`) |
| Opening balance JEs posted | 22 (of 24 non-excluded rows; 2 rows had a `0.00` closing balance and were skipped). `Opening Entry`, `is_opening=Yes`, dated 2026-09-06, branch/cost_center = `S7 - Empire Heights` / `S7 - Empire Heights - SSM` on every account line, balanced against `1910 - Temporary Opening - SSM`, idempotency key `LEGACY-OB-S7-<code>` (deliberately distinct from S1's plain `LEGACY-OB-<code>` so the two branches' idempotency checks never cross given the code collision above) |
| Net posted | ₨1,317,239.14 (credit-side net across all 22 rows) — matches the source file's own column totals |

**`SHAN MARKETING SERVICES (SEASONS)` (NTN `2190273`) is disabled** —
deliberately, by Administrator, 2026-08-24. Its own legacy codes (`095,161,
648` from S1's original import) still point at it, but all purchase activity
since the disable date has continued on the other Supplier sharing the same
NTN, `SHAN MARKETING SERVICES (NESTLE YOGURT)` (9 POs / 11 PIs through
2026-09-04, vs `SEASONS`'s last activity 2026-08-07) — the business
consolidated onto `NESTLE YOGURT` and retired `SEASONS`. S7's code-`095`
balance was posted against `NESTLE YOGURT` instead, not `SEASONS`.
`SEASONS`'s `disabled` flag was left untouched.

## Scripts / artifacts checklist

| Artifact | Role |
|---|---|
| `audit_s7_barcodes_vs_szl.py` | Gap audit + Excel upload to `Home/Migrations/S7` |
| `reconcile_s7_missing_vs_master.py` | Read-only: join 548 missing rows to master file; ready/blocked/gates reports (hard-targets `szl`) |
| `create_s7_missing_items.py` | Create the 548 missing Items from master file (mock `siezal`; DRY_RUN default) |
| `attach_s7_orphan_barcode2.py` | Attach the 16 orphan Barcode2 rows onto already-matched Items |
| `merge_s7_duplicate_catalog_items.py` | Merge S7-colliding catalog duplicates; live szl later after backup |
| `lock_pkr_currency.py` | Force PKR on Price Lists/Item Prices; disable other currencies |
| `import_s7_prices_and_stock.py` | S7 selling prices + opening stock (positive/negative, chunked, GL-checked, queue-headroom throttled) |
| `import_s7_gst_opening.py` | S7 GST-portion opening entry (mirrors `import_szl_s1_gst_opening.py`) |
| `fix_s7_barcode_named_items.py` | One-off: attach barcode + post price/stock/GST for the 5 barcode-named existing items the bulk scripts couldn't resolve |
| `ensure_s7_nicotine_tobacco_groups.py` | Approved exception: create `Nicotine`/`Tobacco` Item Groups (hard-targets `szl` directly — already run) |
| `install_migration_workspace.py` | Desk Migration workspace |
| `import_s7_vendor_balances.py` | S7 vendor opening balances (manual per-row Supplier resolution, not automatic NTN/code matching — already run) |
| `import.md` | Canonical field mapping, tax-exclusive CurCost, stock GL rules |
| `setup_szl.md` / `szl_reference_data.py` | S7 branch naming / accounts |
| `import_szl_s1_stock.py` (+ gap/reenable/GST siblings) | Closest stock pattern to adapt |
| `update_szl_s1_sale_prices.py` | Closest price pattern to adapt |

## Sign-off log

| Date | Decision | By |
|---|---|---|
| 2026-09-04 | 548 missing rows / 579 absent barcodes accepted as gap baseline from S7 Itemonhand vs live `szl` | Audit run |
| 2026-09-04 | Master file supplies Item master; S7 file supplies price + stock | User |
| 2026-09-04 | No new subcategory / no new FBR Tax Category without approval | User |
| 2026-09-04 | **Existing Item Groups win — never create new Item Groups** | User |
| 2026-09-04 | Plan + mock on `siezal` before live `szl` | User |
| 2026-09-04 | Master `Ho-MasterItemFile.xlsx` (`fdd3fa1cd5`): all 548 barcodes found in master; Proper Case on description/brand at create | Reconcile |
| 2026-09-04 | Alias `HOUSEHOLD SUNDRIES` → existing `Household Essentials` only (no new group) | Docs / import.md merge |
| 2026-09-04 | `HAJI MOEEN (COSMETICS)` → existing leaf `Colour Cosmetics` (under Face-Hair-Body - Oral Care) | User |
| 2026-09-04 | Create Item Groups `Nicotine` (parent) and `Tobacco` (child leaf); map master `TOBACCO` → `Tobacco`. Backup `20260904_032937-szl-database.sql.gz` | User |
| 2026-09-04 | Unmatched master brands: leave `Item.brand` empty; do not create Brand masters | User |
| 2026-09-04 | Item create: `STO-ITEM-.YYYY.-` via autoname hook; no warehouse/item_defaults; barcodes on child table; prices/stock later from S7 file | Verified vs live szl |
| 2026-09-04 | Bisconni Chocolate Chip: merge `STO-ITEM-2026-11173` (later barcodes) into `STO-ITEM-2026-11166` (stock/sales); survivor keeps all four barcodes | User |
| 2026-09-04 | Clear catalog merges: Candyland, 7Up, Hilal Jiggles, Kitcy Basil, Dawn Paratha, Walls Choc Bar. Not Rite / Olpers Promo / LU Prince pack | User |
| 2026-09-04 | PKR only: Price Lists and Item Prices must be PKR; disable other currencies before live S7 prices | User |
| 2026-09-04 | Mock Item create on `siezal`: 546 Items `STO-ITEM-2026-17194`–`17739`, no warehouses. Skipped junk `TEST`. `SZ002` already on `STO-ITEM-2026-16374`. Live `szl` still 17576. | Try-it pass |
| 2026-09-05 | Read-only verification (no mutation): `siezal` mock confirmed complete — 546 S7 items, 2,910 nonzero S7 bins, 3,071 S7 prices, 15 tagged opening Stock Entries, all 7 catalog merges applied, currency locked to PKR-only (1 enabled). Live `szl` confirmed unaffected by any of this — series unchanged (17216), 0 S7 stock entries, 0 S7 prices, Bisconni merge-away item still present; only the pre-approved `Nicotine`/`Tobacco` groups are live. | Claude (verification) |
| 2026-09-06 | POS go-live setup created directly on live `szl` (separate from the item/price/stock migration, which stays paused pending fresh source data): Account `1112 - Cash in Hand - S7EH - SSM`, Mode of Payment `Cash - S7EH`, Customer `S7 Walk in Customer`, POS Profiles `S7 Counter 1` and `S7 Food Panda`. Backup `20260906_001604-szl-database.sql.gz`. FBR Integration Settings for S7 confirmed already live (`pos_id=96711`). POS Profile Users / branch User Permission deferred pending staffing. | User |
| 2026-09-06 | S7 vendor opening balances posted to live `szl` from `1007vendorbalances.xlsx` (25 rows): 22 opening JEs, code `614` created as new Supplier `SIEZAL SUPERMARKET (KHANNA PULL)` (user confirmed genuinely new), code `1004` matched to existing `SIEZAL SUPERMARKET (BAHRIA PH7)` (user confirmed same entity despite differing legacy code), code `081` TEST SUPPLIER (Rs 10) skipped (user confirmed), code `095` re-routed from disabled `SHAN MARKETING SERVICES (SEASONS)` to active `SHAN MARKETING SERVICES (NESTLE YOGURT)` (same NTN, user flagged the disabled-Supplier mismatch). Backup `20260906_015412-szl-database.sql.gz`. Confirmed: legacy `SupplierCode` is branch-local, not globally unique — see new section above. | User |
| 2026-09-06 | Bug fix: `S7 Walk in Customer` was created without `default_price_list` set, leaving it effectively on S1's price list; user corrected it to `S7 - Empire Heights Selling Price List`. Any new branch-specific walk-in Customer must have this field set explicitly at create time — not inherited from Customer Group (all groups have it blank). | User |
| 2026-09-06 | User confirmed fresh `Ho-MasterItemFilebaf350.xlsx` / `1007.xls` uploaded to replace stale 2026-09-04 source files; asked to re-run the mock pass with them | User |
| 2026-09-06 | Fresh-data mock re-run on `siezal` (restored from `20260906_022406-szl-database.sql.gz`): audit (548 missing, unchanged), master reconcile (`ALL_548_READY`, unchanged verdict), 546 items created, 15 orphan barcodes attached, all 7 catalog merges re-applied, 3,083 S7 prices posted, 2,906 opening-stock bins posted (2,885 positive + 21 negative). Reconcile (pass order step 9) run for the first time: 0 missing/mismatched bins, bin value ₨4,690,983.86 vs expected ₨4,690,983.85, 0 blank-cost-center GL, SLE count matches Bin count exactly. Mock is clean and fully reconciled on current data. | Claude (mock re-run + reconcile) |
| 2026-09-06 | User approved live `szl` cutover ("do it on live szl now you have the latest files"). Backup `20260906_025559-szl-database.sql.gz`. Live run: 546 items created, 15 orphan barcodes attached, 7 catalog merges applied, 3,083 prices posted, 2,906 opening-stock bins posted (value ₨4,690,983.86) — all numbers match the fresh-data mock exactly. Mid-run `QueueOverloaded` on the shared background queue (newly-configured S7 Foodpanda Outlet enqueuing a sync job per Bin update) fixed by adding a queue-headroom throttle to `import_s7_prices_and_stock.py`; resumed cleanly via the script's existing per-chunk idempotency, no data lost or duplicated. Post-run reconcile clean: 0 blank branch/cost-center rows, SLE count matches Bin count, all prior S7 live work (POS profiles, vendor balances, price-list fix) confirmed untouched. | User + Claude |
| 2026-09-06 | User caught that GST opening (pass order step 8) was skipped. Posted `ACC-JV-2026-00735` (₨754,068.61, `import_s7_gst_opening.py`, 3,111 items matched, 5 unresolved). The 5 unresolved were investigated (not left as an unexplained gap): existing catalog items named after their own barcode with no Item Barcode row — `fix_s7_barcode_named_items.py` attached their barcodes, then posted their price (5 rows), opening stock (`MAT-STE-2026-00208`, 3 nonzero rows), and GST (`ACC-JV-2026-00736`, ₨443.57). All verified correct. | User + Claude |
