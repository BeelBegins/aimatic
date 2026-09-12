# S4 Wallayat Complex migration

Branch go-live plan for **S4 - Wallayat Complex** on company Siezal Super
Market (SSM), site `szl` (production) after mock testing on `siezal`. Follows
the same pattern as `s7_empire_heights.md` (the most recently completed
branch cutover, live on `szl` since 2026-09-06) — reuse its file-role split,
hard gates, and pass order rather than inventing a new process.

Reusable mapping rules stay in `import.md`. This file records **S4-only
verdicts, gates, file roles, and pass order**. Do not invent a second mapping
system here.

## Status (2026-09-13, live items/prices/stock posted)

| Phase | Status |
|---|---|
| Branch / warehouse / cost center / price list masters | Already present on live `szl` (`S4 - Wallayat Complex`, warehouse `S4 - Wallayat Complex - SSM` + `Rejected` variant, cost center `S4 - Wallayat Complex - SSM`, Selling + Foodpanda Price Lists) — see `szl_reference_data.py` (`ledger_suffix=S4WC`, inter-branch payable account number `2142`) |
| Account (`Cash in Hand - S4WC`), Mode of Payment, walk-in Customer, POS Profile, FBR Integration Settings | **Not yet created** — matches S7's separate "POS go-live setup" step, still pending here |
| Live `szl` S4 data | **Items, selling prices, and opening stock posted 2026-09-13** — see Live cutover. Vendor balances and POS go-live still pending. Do not re-run Item create or opening-stock posting. |
| Source files received | **Done** 2026-09-12: `Ho-MasterItemFiled5b674.xlsx` (master, File `9872249e71`), `1004stockposition.xls` (branch stock/price, File `3b594c134a`), `1004vendorbalances.xlsx` (vendor opening balances, File `16ee8a6a31`). "1004" is S4's own FBR-style branch code, same convention as S7's "1007" |
| Mock restore of `szl` backup onto `siezal` | **Done** 2026-09-12 ~23:39 PKT — source `20260912_233859-szl-database.sql.gz` (+files/private-files tars), taken after the same-day cost-center GL fix and after the three S4 source files were uploaded, so the mock includes both. Verified: `siezal`'s GL missing-`cost_center` counts read 0 across all accounts (matches live `szl` post-fix), S4 branch/warehouse present, all three S4 files present on disk and in the `File` doctype |
| Barcode gap audit vs catalog (mock `siezal`) | **Done** 2026-09-12 — see below |
| Master reconcile (mock `siezal`) | **Done** 2026-09-13 — 4,717 / 4,897 ready, 180 blocked on 5 unknown subcategories; user resolved all 5 (see Sign-off log) |
| New Item Group `Fruits & Vegetables` | **Done** 2026-09-13 on mock and live `szl` — leaf under existing `Food Items`, via `ensure_s4_fruits_vegetables_group.py` |
| Item create | **Done** 2026-09-13 on mock and live `szl` — 4,882 new Items inserted (`STO-ITEM-2026-17898`–`22779`) plus pre-existing barcode `SZ002` resolved to `STO-ITEM-2026-16374`, for 4,883 successfully processed rows. Earlier output called all 4,883 "created"; the database range count is the authoritative 4,882. 0 failed, 13 rows skipped, 0 blocked. |
| Orphan Barcode2 attach | **Done** 2026-09-13 on mock and live `szl` — 22/22 attached, 0 blocked, via `attach_s4_orphan_barcode2.py` |
| Catalog duplicate check (mock `siezal`) | **Done** 2026-09-13 — 6 candidates found, **0 recommended for merge** (see below); differs from S7, which had 7 genuine merges |
| Colgate Premier/Twister correction | **Done** 2026-09-13 on mock after `20260913_011949-siezal-*`, then on live after `20260913_013922-szl-*`. Premier remains `STO-ITEM-2026-09751`; barcode `8886950093352` moved to new `STO-ITEM-2026-22780` (`Colgate Twister M`). Existing Premier activity unchanged. |
| S4 prices + opening stock | **Done on live `szl` 2026-09-13** (mock price/stock posting skipped by user). Dry-run 0 blockers: 13,219 prices, 7,485 positive and 801 negative stock rows. Posted those totals; SLE=Bin=8,286. |
| Vendor balances, POS go-live | **Not started** — explicit approval required per phase. |

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
and Item-create pass. The master-reconcile and Item-create passes below have since resolved this gap on the mock.

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

## Catalog duplicate check (2026-09-13, on `siezal` mock)

Script: `detect_s4_duplicate_catalog_items.py` — detects S4 stock rows whose
Barcode1/Barcode2 resolve to two *different* existing Items (the situation
S7 had 7 signed merge pairs for). Read-only: reports candidates with stock/
sales activity for a human decision; does not merge anything. All these
Items pre-date S4 (created mid-July, well before any S4 file), so this is
about the *shared catalog*, not S4-specific data.

544 stock rows carry both barcodes; 6 resolved to a Barcode1/Barcode2 pair
split across two different Items:

| Item A | Item B | Verdict |
|---|---|---|
| Olpers Dairy Cream 200Ml | Olpers Dairy Cream 200Ml Promo | **Not a duplicate** — S7's runbook already reviewed this exact pair and explicitly decided "Not merged: ... Olpers regular vs Promo" |
| Lu Prince Chocolate Big S-Pack | Lu Prince Chocolate 57Gm | **Not a duplicate** — S7's runbook already reviewed this exact pair: "Not merged: ... LU Prince 57gm vs Big S-Pack (Box UOM)" |
| Dove Hair Fall Rescue Conditioner 180Ml | Dove Hair Fall Bio Protein Shampoo 360Ml | **Not a duplicate** — different products (conditioner vs shampoo, different sizes); Barcode1/Barcode2 pairing in the source row looks like a data-entry coincidence, not the same SKU twice |
| Enchanteur Charming Talcum Powder 125Gm | Enchanteur Stunning Talcum Powder 125Gm | **Not a duplicate** — different scent variants ("Charming" vs "Stunning"), not the same product re-entered |
| Bisconni Chocolate Chip Cookies Rs-20 | Promo Chocolate Chip Rs 10 | **Not a duplicate** — different name and price point; S7's actual approved Bisconni merge was between two Items with the *identical* name/price, not this |
| Knorr Chicken Noodles 200Gm | Knorr Chicken Noodles Family Pack | **Not a duplicate** — different pack sizes, genuinely separate SKUs |

**Verdict: 0 merges recommended for this pass.** Unlike S7 (which found 7
genuine same-name catalog splits), the S4 onhand file's Barcode1/Barcode2
collisions all turned out to be different products/variants that happen to
share a row, not the same item entered twice. Treat this as informational,
not applied — no `merge_s4_duplicate_catalog_items.py` needed unless a
future pass surfaces an actual name-identical pair.

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
3. ~~Master reconcile (read-only)~~ — **done** 2026-09-13.
4. ~~Create missing Items on `siezal` mock~~ — **done**: 4,882 inserted plus pre-existing `SZ002` resolved.
5. ~~Attach orphan Barcode2 (22 rows).~~ **Done**: 22/22.
5b. Check for S4-side catalog duplicates (two Items split across Barcode1/
   Barcode2, as S7 had 7 pairs) — **done**: six source collisions remain separate; the later user-confirmed Colgate Premier/Twister master error was split on mock then live.
6. S4 selling prices — **done on live `szl` 2026-09-13** (mock posting skipped).
7. S4 opening stock — **done on live `szl` 2026-09-13**; branch/cost center stamped; SLE=Bin=8,286.
8. Vendor opening balances from `1004vendorbalances.xlsx` (238 rows) — same
   shape as `supplierimport.md` / S7's vendor balances step. **Check for
   `SupplierCode` collisions against other branches' legacy codes before any
   automatic matching** — S7 discovered legacy codes are branch-local, not
   globally unique (code `614`/`1004` collision), so this must be manual
   per-row resolution again, not automatic NTN/code matching.
9. POS go-live setup (Account, Mode of Payment, walk-in Customer, POS
   Profile, FBR Integration Settings) — mirror S1/S7 field-for-field, watch
   for the same `default_price_list` gap that bit S7's walk-in Customer.
10. ~~Live cutover of Items/prices/stock on `szl`~~ — **done** 2026-09-13; vendor and POS remain.

## Live cutover (2026-09-13)

User skipped remaining mock price/stock posting: "just take a szl backup do
it all there we are running out of time." Codex took backup
`20260913_013922-szl-*` (database, public files, private files; gzip/tar
integrity checked) while series was still `17897` and S4 prices/stock were
zero, then hit a usage limit before the first write. This session flipped
`TARGET_SITE` from `siezal` to `szl` on the four mutating scripts, confirmed
no POS/Item/SLE writes since that backup, and applied the mock order on live.

| Step | Live result |
|---|---|
| Fruits & Vegetables group | Created under `Food Items` |
| Item create | 4,882 inserted (`STO-ITEM-2026-17898`–`22779`); 4,883 processed including pre-existing `SZ002` → `STO-ITEM-2026-16374`; 13 skipped; 0 failed; 0 item_defaults warehouses |
| Orphan Barcode2 attach | 22/22 attached, 0 blocked |
| Colgate split | Twister barcode moved to `STO-ITEM-2026-22780`; Premier `STO-ITEM-2026-09751` activity unchanged (2 nonzero bins, qty 7, 6 SLE, 7 prices) |
| S4 prices | 13,219 rows, 0 missing, 0 mismatched |
| S4 opening stock | 38 Material Receipt + 5 Material Issue chunks (`MAT-STE-2026-00232`–`00274`); 8,286 SLE = 8,286 nonzero bins; 0 blank branch/cost-center on Stock Entry Detail and GL. Plan exclusive value ₨16,259,120.16; posted Bin value ₨16,259,122.55 (₨2.39). Six qty rows differ by 0.005 because System Settings `float_precision=2` (e.g. 11.925 → 11.92). |

Rollback: restore `20260913_013922-szl-database.sql.gz` plus the matching
files/private-files tars. Do not re-run Item create or opening-stock posting.

## Scripts / artifacts checklist

| Artifact | Role |
|---|---|
| `audit_s4_barcodes_vs_szl.py` | Gap audit + Excel upload to `Home/Migrations/S4` (done) |
| `reconcile_s4_missing_vs_master.py` | Read-only master reconcile — done |
| `ensure_s4_fruits_vegetables_group.py` | Approved exception: create `Fruits & Vegetables` leaf under `Food Items` — done on mock and live |
| `create_s4_missing_items.py` | Create missing Items from master file — done on mock and live (4,882 inserted + pre-existing `SZ002` resolved). `TARGET_SITE` is now `szl`. |
| `attach_s4_orphan_barcode2.py` | Attach orphan Barcode2 rows — done on mock and live (22/22) |
| `detect_s4_duplicate_catalog_items.py` | Read-only catalog duplicate detection — done, 0 merges recommended |
| `split_s4_colgate_twister.py` | User-approved Colgate product split — applied on mock and live (`STO-ITEM-2026-22780`) |
| `import_s4_prices_and_stock.py` | S4 selling prices + opening stock — posted on live `szl`; `TARGET_SITE` is now `szl`. Do not re-post stock. |
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
| 2026-09-13 | 4,883 rows processed on `siezal` mock: **4,882 new Items inserted** and pre-existing `SZ002` resolved; 0 failed, 0 blocked, 13 skipped. Earlier output conflated processed with inserted; database range count confirms 4,882. **Live `szl` untouched** | Claude (executed + verified), User (approved decisions) |
| 2026-09-13 | 22 orphan Barcode2 rows attached on `siezal` mock via `attach_s4_orphan_barcode2.py`: dry-run clean (0 blocked), applied, 22/22 attached | Claude (executed + verified) |
| 2026-09-13 | Catalog duplicate check via `detect_s4_duplicate_catalog_items.py`: 6 candidates found, all reviewed and rejected as merges — 2 match S7's own already-declined pairs (Olpers regular/Promo, LU Prince 57gm/Big S-Pack), 4 are different products/variants coincidentally paired in the source row. 0 merges applied | Claude (analysis) |
| 2026-09-13 | Colgate Premier and Twister are different products. After verified mock backup `20260913_011949-siezal-*`, moved barcode `8886950093352` to new `STO-ITEM-2026-22780` while preserving Premier and all historical activity. Applied and idempotency-verified on `siezal` only | User (decision + mock approval), Codex (executed + verified) |
| 2026-09-13 | Combine duplicate source rows for the same product into the existing Item; leading-zero barcode variants are the same product; combine quantities and use the higher selling price unless explicitly overridden. The approved 25-Item price map is recorded in `import_s4_prices_and_stock.py`; explicit choices include 1319, 175, 188, 2785, 85, 230, 219, 819, 185, 1085, 60, 1235, 499, 1920, and 2759 for the reviewed products | User |
| 2026-09-13 | Combined net-zero stock must be zero. Skip residual values for Guard Rice (`STO-ITEM-2026-06127`, -53.19), Nestle Nan (`STO-ITEM-2026-10318`, 0.66), and Dairy Life Ghee (`STO-ITEM-2026-17790`, 1071.10) | User |
| 2026-09-13 | Final price/stock dry-run: 13,284 source rows; 13 exclusions; 13,232 resolved Items; 39 duplicate groups combined; 25 approved price choices; 13,219 prices; 7,485 positive and 801 negative stock rows; 0 blockers. Postable signed value after approved zero-net skips: inclusive 18,859,337.39; exclusive 16,259,120.16. **Nothing posted yet** (mock) | Codex (verified) |
| 2026-09-13 | Skip remaining mock price/stock posting; take a current `szl` backup and apply the full prepared Item/price/stock cutover on live | User |
| 2026-09-13 | Live `szl` cutover after `20260913_013922-szl-*`: Fruits & Vegetables group; 4,882 Items `STO-ITEM-2026-17898`–`22779` plus `SZ002` resolved; 22 orphan barcodes; Colgate Twister `STO-ITEM-2026-22780`; 13,219 prices; 8,286 bins/SLE. Bin value ₨16,259,122.55 vs plan ₨16,259,120.16. Vendor/POS still pending. | Cursor (executed + verified), User (approved live apply) |
