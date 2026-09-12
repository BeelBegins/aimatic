# S4 Wallayat Complex migration

Branch go-live plan for **S4 - Wallayat Complex** on company Siezal Super
Market (SSM), site `szl` (production) after mock testing on `siezal`. Follows
the same pattern as `s7_empire_heights.md` (the most recently completed
branch cutover, live on `szl` since 2026-09-06) — reuse its file-role split,
hard gates, and pass order rather than inventing a new process.

Reusable mapping rules stay in `import.md`. This file records **S4-only
verdicts, gates, file roles, and pass order**. Do not invent a second mapping
system here.

## Status (2026-09-12, initial)

| Phase | Status |
|---|---|
| Branch / warehouse / cost center / price list masters | Already present on live `szl` (`S4 - Wallayat Complex`, warehouse `S4 - Wallayat Complex - SSM` + `Rejected` variant, cost center `S4 - Wallayat Complex - SSM`, Selling + Foodpanda Price Lists) — see `szl_reference_data.py` (`ledger_suffix=S4WC`, inter-branch payable account number `2142`) |
| Account (`Cash in Hand - S4WC`), Mode of Payment, walk-in Customer, POS Profile, FBR Integration Settings | **Not yet created** — matches S7's separate "POS go-live setup" step, still pending here |
| Live `szl` S4 data | **Clean slate** — 0 Item Prices, 0 nonzero Bins, 0 Stock Ledger Entries on any S4 warehouse as of 2026-09-12. No re-run hazard (unlike S7, nothing has been posted yet) |
| Source files received | **Done** 2026-09-12: `Ho-MasterItemFiled5b674.xlsx` (master, File `9872249e71`), `1004stockposition.xls` (branch stock/price, File `3b594c134a`), `1004vendorbalances.xlsx` (vendor opening balances, File `16ee8a6a31`). "1004" is S4's own FBR-style branch code, same convention as S7's "1007" |
| Mock restore of `szl` backup onto `siezal` | **Done** 2026-09-12 ~23:39 PKT — source `20260912_233859-szl-database.sql.gz` (+files/private-files tars), taken after the same-day cost-center GL fix and after the three S4 source files were uploaded, so the mock includes both. Verified: `siezal`'s GL missing-`cost_center` counts read 0 across all accounts (matches live `szl` post-fix), S4 branch/warehouse present, all three S4 files present on disk and in the `File` doctype |
| Barcode gap audit vs catalog (mock `siezal`) | **Done** 2026-09-12 — see below |
| Master reconcile (mock `siezal`) | **Done** 2026-09-13 — 4,717 / 4,897 ready, 180 blocked on 5 unknown subcategories; user resolved all 5 (see Sign-off log) |
| New Item Group `Fruits & Vegetables` (mock `siezal`) | **Done** 2026-09-13 — leaf under existing `Food Items`, via `ensure_s4_fruits_vegetables_group.py`. **Not yet created on live `szl`** |
| Item create (mock `siezal`) | **Done** 2026-09-13 — 4,883 Items created (`STO-ITEM-2026-17898`–`22779`... one series number, `SZ002`, already existed, matching the S7-era note that it's already on `STO-ITEM-2026-16374`), 0 failed, 13 rows skipped (junk/blank subcat), 0 blocked. Verified: 0 `item_defaults`/warehouse leakage, 0 duplicate barcodes, exact expected counts landed in each aliased Item Group (Fruits & Vegetables 90, Electronic Items 45, Wrist Watches 31, Animal - Pet Items 1). **Not yet created on live `szl`** |
| Prices, opening stock, vendor balances, POS go-live | **Not started** — do not proceed without explicit approval per phase, same as S7 |

## Barcode gap audit (2026-09-12, on `siezal` mock)

Script: `audit_s4_barcodes_vs_szl.py` — adapted from `audit_s7_barcodes_vs_szl.py`.
Two differences from the S7 script, both because this is a fresh first run
rather than a mock re-run of an already-live branch:

- Parses `1004stockposition.xls` directly with `xlrd` (it reads cleanly; no
  libreoffice-to-CSV conversion step needed, unlike the S7 script's `1007.xls`
  path).
- `ALLOWED_SITES = ("siezal", "szl")` instead of a single hard target, since
  this first run is deliberately against the mock, not live `szl`.

Read-only: queries `Item` / `Item Barcode` only, writes report `.xlsx` files
under `/tmp` and uploads them as `File` docs to `Home/Migrations/S4` on the
site it's run against. No Item/price/stock writes.

| Metric | Count |
|---|---|
| Source rows (`1004stockposition.xls`) | 13,284 |
| Matched against existing catalog | 8,387 |
| **Missing rows** (neither barcode resolves) | **4,897** |
| Unique barcodes in file | 13,824 |
| Unique barcodes present in catalog | 8,849 |
| Unique barcodes absent from catalog | 4,975 |
| Matched rows with only Barcode2 missing (orphan) | 22 |
| Catalog size at time of audit | 18,272 Items, 20,052 Item Barcodes |

Result workbooks uploaded to `Home/Migrations/S4` (`/app/file/view/home/Migrations/S4`
on `siezal`): `s4_missing_items_*.xlsx` (4,897 rows), `s4_absent_barcodes_*.xlsx`
(4,975 rows), `s4_orphan_barcode2_*.xlsx` (22 rows), `s4_barcode_summary_*.json`.

**Gap is proportionally much larger than S7's** (4,897 / 13,284 ≈ 37% missing,
vs S7's 548 / 3,121 ≈ 18%) — expect a correspondingly larger master-reconcile
and Item-create pass. Not yet reconciled against the master item file; that's
the next read-only step, same as S7's "Master reconcile" phase.

## Master reconcile (2026-09-13, on `siezal` mock)

Script: `reconcile_s4_missing_vs_master.py` — adapted from
`reconcile_s7_missing_vs_master.py`, `EXISTING_GROUP_ALIASES` carried over
unchanged (same underlying master-file taxonomy; every alias target is an
Item Group that already concretely exists). Read-only: no Items / groups /
tax categories / brands created.

| Metric | Count |
|---|---|
| Master file rows | 37,892 |
| S4 missing rows (input) | 4,897 |
| **Ready to create** | **4,717** (96.3%) |
| Blocked | 180 (3.7%) |
| Not found in master at all | 0 |
| Ambiguous master match | 0 |
| Unknown/blank FBR Tax Category | **0** — every ready row resolved cleanly, no FBR gate needed |
| Brand left blank (no existing match; not a blocker) | 157 rows / 27 distinct brand names |

**Blocked breakdown — all 180 rows are a subcategory gate, nothing else:**

| SubCatName in master | Rows | Candidate existing Item Group (unconfirmed — needs approval, not applied) |
|---|---|---|
| `FRUITES & VEGETABLES` | 90 | **None found** — no existing group covers fresh produce; genuinely new territory, not a spelling/alias fix |
| `ELECTONIC ITEMS` | 45 | `Electronic Items` (typo: missing "r" — same pattern as S7's alias fixes) |
| `WATCH` | 31 | `Wrist Watches` |
| (blank SubCatName) | 9 | — master row has no subcategory at all; needs a decision, not a group |
| `TESTING` | 4 | Likely junk/test rows, same as S7's skipped `TEST` item — candidate to exclude rather than map |
| `PET ACCESSORIES` | 1 | `Animal - Pet Items` |

Per the hard gates above, none of these candidate mappings are applied —
this table is informational only, same as S7's `Unknown SubCats` gate sheet.
Needs explicit per-row approval before `reconcile_s4_missing_vs_master.py`'s
`EXISTING_GROUP_ALIASES` is extended and re-run.

Reports uploaded to `Home/Migrations/S4`: `s4_create_ready_*.xlsx` (4,717
rows), `s4_create_blocked_*.xlsx` (180 rows), `s4_approval_gates_*.xlsx`
(unknown subcats / FBR / blank-brand sheets), `s4_master_reconcile_summary_*.json`.

## File roles (do not mix) — same split as S7

| File | Use for | Do **not** use for |
|---|---|---|
| **`Ho-MasterItemFiled5b674.xlsx`** (master) | Create the ~4,897 missing Items: name, barcodes, item group, brand, FBR tax category, MRP, and any other master fields present | S4 opening stock qty; S4 branch selling price as sole price source |
| **`1004stockposition.xls`** (S4 onhand) | Branch **stock** (`Quantity` + tax-exclusive `Cost Price`) and S4 **selling prices** (`Sale Price`) for every resolvable barcode | Inventing Item Group / FBR Tax Category; creating Items that lack master fields |

## Hard gates — identical to S7, not renegotiated per branch

Carried over unchanged from `s7_empire_heights.md` (see that file for full
rationale); do not relax any of these for S4 without the same kind of
explicit approval S7 required:

1. Existing Item Groups win — never create new ones (same pre-approved
   `Nicotine`/`Tobacco` exception may not apply here; check master
   `SubCatName` values against S4's own data before assuming S7's alias
   table applies verbatim).
2. No new FBR Tax Category — blank/unmatched category blocks the row.
3. No new Brand — unmatched brand leaves `Item.brand` empty.
4. Dry-run / mock first — all create/post scripts default `DRY_RUN = True`.
   Live `szl` mutation requires explicit approval, current verified `szl`
   backup, expected totals, rollback path.
5. Mock site is `siezal` — restore a current `szl` backup before any S4
   create/post testing (**done** 2026-09-12, see Status table).
6. Idempotent re-runs — no duplicate Items, barcodes, Item Prices, or Stock
   Entry chunks.
7. `item_code` via naming series (`STO-ITEM-.YYYY.-`, currently at
   `STO-ITEM-2026-17897`) — never force legacy barcode into `item_code`.
8. Items are not linked to any warehouse at create time.

## Planned pass order (mock on `siezal`, then live `szl`)

Same shape as S7's:

1. ~~Backup + restore~~ — **done** 2026-09-12.
2. ~~Barcode gap audit on mock~~ — **done** 2026-09-12 (above).
3. **Master reconcile (read-only)** — join the 4,897 missing barcodes to
   `Ho-MasterItemFiled5b674.xlsx`. Produce ready / blocked-subcategory /
   blocked-FBR / blocked-unmatched reports, same shape as S7's
   `reconcile_s7_missing_vs_master.py`. **Not started.**
4. Create missing Items (dry-run → approve → apply) on `siezal` mock only.
5. Attach orphan Barcode2 (22 rows).
5b. Check for S4-side catalog duplicates (two Items split across Barcode1/
   Barcode2, as S7 had 7 pairs) — not yet checked for S4.
6. S4 selling prices onto the S4 branch Selling Price List.
7. S4 opening stock into the S4 warehouse, branch/cost center stamped on
   header and every row.
8. Vendor opening balances from `1004vendorbalances.xlsx` (238 rows) — same
   shape as `supplierimport.md` / S7's vendor balances step. **Check for
   `SupplierCode` collisions against other branches' legacy codes before any
   automatic matching** — S7 discovered legacy codes are branch-local, not
   globally unique (code `614`/`1004` collision), so this must be manual
   per-row resolution again, not automatic NTN/code matching.
9. POS go-live setup (Account, Mode of Payment, walk-in Customer, POS
   Profile, FBR Integration Settings) — mirror S1/S7 field-for-field, watch
   for the same `default_price_list` gap that bit S7's walk-in Customer.
10. Live cutover on `szl` after mock is fully reconciled and approved.

## Scripts / artifacts checklist

| Artifact | Role |
|---|---|
| `audit_s4_barcodes_vs_szl.py` | Gap audit + Excel upload to `Home/Migrations/S4` (done) |
| `reconcile_s4_missing_vs_master.py` | Read-only master reconcile — done |
| `ensure_s4_fruits_vegetables_group.py` | Approved exception: create `Fruits & Vegetables` leaf under `Food Items` — done on mock, not yet on live `szl` |
| `create_s4_missing_items.py` | Create missing Items from master file — done on mock (4,883 created), not yet on live `szl` |
| *(not yet written)* `import_s4_prices_and_stock.py` | S4 selling prices + opening stock |
| *(not yet written)* `import_s4_vendor_balances.py` | S4 vendor opening balances |
| `import.md` | Canonical field mapping, tax-exclusive CurCost, stock GL rules |
| `setup_szl.md` / `szl_reference_data.py` | S4 branch naming / accounts (already defines S4) |

## Sign-off log

| Date | Decision | By |
|---|---|---|
| 2026-09-12 | Follow the S7 pattern for S4 (file roles, hard gates, pass order) rather than a new process | User |
| 2026-09-12 | Restored `20260912_233859-szl-database.sql.gz` (+files) onto `siezal` as the S4 mock baseline; verified GL cost_center fix and S4 masters both present post-restore | User (approved), Claude (executed + verified) |
| 2026-09-12 | Barcode gap audit run against `siezal` mock (not live `szl`, since S4 has no pre-existing live data to protect either way, but mock is safer default for reporting writes) — 4,897 missing rows accepted as gap baseline | Audit run |
| 2026-09-13 | `FRUITES & VEGETABLES` (90 rows): create new leaf Item Group `Fruits & Vegetables` under existing parent `Food Items` — no existing group covers fresh produce, genuinely new territory (unlike the other 3 subcat gates below) | User |
| 2026-09-13 | Approved 3 typo/near-match aliases to existing groups: `ELECTONIC ITEMS`→`Electronic Items`, `WATCH`→`Wrist Watches`, `PET ACCESSORIES`→`Animal - Pet Items` | User |
| 2026-09-13 | `TESTING` (4 rows) and blank/literal-`NULL` SubCatName (9 rows): excluded, same treatment as S7's skipped `TEST`/`TEST3` junk rows | User |
| 2026-09-13 | New Item Group `Fruits & Vegetables` created on `siezal` mock only via `ensure_s4_fruits_vegetables_group.py` (idempotent, allows mock or live) | Claude (executed), User (approved) |
| 2026-09-13 | 4,883 Items created on `siezal` mock via `create_s4_missing_items.py` (adapted from `create_s7_missing_items.py`): 0 failed, 0 blocked, 13 skipped as junk/blank, 1 (`SZ002`) already existed pre-S4 (matches the S7-era note). Verified 0 warehouse leakage, 0 duplicate barcodes, exact per-group counts. **Live `szl` still untouched** — this was mock only | Claude (executed + verified), User (approved via master-reconcile decisions) |
