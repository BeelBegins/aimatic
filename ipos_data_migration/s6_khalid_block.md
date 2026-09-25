# S6 Khalid Block migration

Branch go-live for **S6 - Khalid Block** on company Siezal Super Market
(SSM), live site `szl`. Same file roles, hard gates, and pass order as
`s4_wallayat_complex.md`. Do not invent a second mapping system. Do not
re-run S1/S4/S7 importers.

Live-only: no `siezal` mock. Dry-run every mutating script; apply only after
a verified `szl` backup.

## Status (2026-09-21)

| Phase | Status |
|---|---|
| Branch / warehouse / cost center / price lists | Already present (`S6 - Khalid Block`, `S6 - Khalid Block - SSM`, Selling + Foodpanda lists, PKR, empty bins/prices). Ledger suffix `S6KB`, inter-branch payable `2144`. |
| Source files | `Home/s6&s5 migration`: master `Ho-MasterItemFilea16c3b.xlsx` (File `c02b75bf48`), `1006stockposition.xls` (File `e195467e67`, SHA256 `46e04873…`, 10,679 barcode rows), `1006vendorbalances.xlsx` (27 rows; skip `TEST SUPPLIER`), `productsS6.xlsx` (File `8fb5450a8e`, SHA256 `e351494e…`, 15,000 portal rows). |
| Mock on `siezal` | Skipped by user (live-only). |
| Barcode audit | **Done** 600 missing, 2 orphan Barcode2. |
| Item create | **Done** 587 Items `STO-ITEM-2026-22895`–`23481`; 12 TESTING/NULL skipped. |
| Orphan Barcode2 | **Done** 2/2. |
| Catalog duplicates | 5 pairs, none merged (same S4 non-duplicates). |
| Prices + opening stock | **Done** 10,312 PKR prices; 3,173 SLE=Bin; value ₨3,141,739.11 vs ₨3,141,739.13; 0 blank dimensions. `MAT-STE-2026-00296`–`00312`. |
| Vendor JEs | **Done** 24 `LEGACY-OB-S6-*`. Held: TEST SUPPLIER; sister `1004` Bahria PH7 ₨-458,751.26. |
| POS | **Done** cash `1114`; profiles `S6 Counter 1`, `S6 FP1` (renamed from Food Panda). Cashiers `israr@`, `fps6@` (POS User + S6 Branch permission). Customer `S6 Home Delivery` exists (S6 selling list); no S6 HD terminal/cashier yet. |
| Foodpanda prices | **Done** 9,885 PKR prices on `S6 - Khalid Block Foodpanda Price List` only (`import_s6_foodpanda_prices.py`). |
| FBR | **Enabled** by user: `branch_code=1006`, `pos_id=195582` (unique vs S1/S4/S5/S7). |

## File roles (do not mix)

| File | Use for | Do not use for |
|---|---|---|
| `Ho-MasterItemFilea16c3b.xlsx` | Create missing Items (name, barcodes, group, brand, FBR, MRP) | S6 qty / S6 selling price as sole source |
| `1006stockposition.xls` | S6 stock (`Quantity` + tax-exclusive `Cost Price`) and S6 selling prices (`Sale Price`) | Inventing Item Group / FBR / Brands |
| `1006vendorbalances.xlsx` | Opening JEs `LEGACY-OB-S6-*` | Item create |
| `productsS6.xlsx` | S6 Foodpanda prices | Selling prices / stock |

Crystal garbles the stock-file header; parse seven columns positionally.

## Hard gates (same as S4/S7)

1. Existing Item Groups only (S4 aliases including Fruits & Vegetables). Unknown `SubCatName` blocks.
2. No new FBR Tax Category. No new Brand (leave blank).
3. PKR only on these Price Lists / Item Prices. Do not run `lock_pkr_currency.py` on live.
4. Dry-run default. Live apply needs backup, totals, rollback.
5. Idempotent. Verify Bin/SLE/GL, not printed stats.
6. `item_code` from `STO-ITEM-.YYYY.-`. No warehouse on Item create.
7. Stamp branch and cost center on Stock Entry header and every row; fail if GL is blank.
8. Do not post if `S6 - Khalid Block - SSM` already has nonzero bins without these opening tags.

## Pass order (live `szl`)

1. Verified `szl` backup (database + files).
2. `audit_s6_barcodes_vs_szl.py` (read-only + File upload to `Home/Migrations/S6`).
3. `reconcile_s6_missing_vs_master.py` — stop if unknown groups/FBR.
4. `create_s6_missing_items.py` dry-run then apply.
5. `attach_s6_orphan_barcode2.py` (copy existing barcode UOM; do not change stock UOM).
6. `detect_s6_duplicate_catalog_items.py` — merge only if signed.
7. `import_s6_prices_and_stock.py` dry-run then apply (`S6-ONHAND-IMPORT-2026-09-20`).
8. `import_s6_vendor_balances.py` (NTN-first; skip TEST; sister-store map from S4).
9. `setup_s6_pos_golive.py` (cash `1114`, `Cash - S6KB`, walk-in default price list, profiles disabled).
10. `setup_s6_fbr.py` (disabled copy). Enable only after unique `pos_id`.
11. `import_s6_foodpanda_prices.py` — write only `S6 - Khalid Block Foodpanda Price List`.
12. `enable_s5_s6_pos.py` after unique FBR `pos_id`.

Confirm S1/S4/S7 bins and prices unchanged after stock post.

## Scripts

| Artifact | Role |
|---|---|
| `audit_s6_barcodes_vs_szl.py` | Gap audit |
| `reconcile_s6_missing_vs_master.py` | Read-only master reconcile |
| `create_s6_missing_items.py` | Missing Items from master |
| `attach_s6_orphan_barcode2.py` | Orphan Barcode2 |
| `detect_s6_duplicate_catalog_items.py` | Catalog split report |
| `import_s6_prices_and_stock.py` | PKR prices + opening stock |
| `import_s6_vendor_balances.py` | Vendor opening JEs |
| `setup_s6_pos_golive.py` | Cash / MoP / customers / POS |
| `setup_s6_fbr.py` | Disabled FBR copy (user later set `pos_id=195582` and enabled) |
| `import_s6_foodpanda_prices.py` | PKR Foodpanda prices from `productsS6.xlsx` |
| `enable_s5_s6_pos.py` | Enable S5/S6 profiles after unique FBR IDs |
| `assign_s5_s6_pos_users.py` | Cashiers, FP1 rename, S5 HD (no passwords in file) |
