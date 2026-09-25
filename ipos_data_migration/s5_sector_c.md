# S5 Sector C migration

Branch go-live for **S5 - Sector C** on company Siezal Super Market (SSM),
live site `szl`. Same file roles, hard gates, and pass order as
`s4_wallayat_complex.md` / `s6_khalid_block.md`. Post **after** S6 in the
same session. Do not re-run S1/S4/S6/S7 importers.

Live-only: no `siezal` mock. Dry-run every mutating script; apply only after
a verified `szl` backup (the pre-cutover backup, or a second backup taken
after S6).

## Status (2026-09-21)

| Phase | Status |
|---|---|
| Branch / warehouse / cost center / price lists | Already present (`S5 - Sector C`, `S5 - Sector C - SSM`, Selling + Foodpanda lists, PKR, empty bins/prices). Ledger suffix `S5SC`, inter-branch payable `2143`. |
| Source files | `Home/s6&s5 migration`: shared master `Ho-MasterItemFilea16c3b.xlsx` (File `c02b75bf48`), `1005stockposition.xls` (File `ff345d08d5`, SHA256 `7f5ec5fb…`, 6,392 barcode rows), `1005Vendorbalances.xlsx` (79 rows), `productsS5.xlsx` (File `39644ed46d`, SHA256 `4eac93fe…`, 15,000 portal rows). |
| Mock on `siezal` | Skipped by user (live-only). |
| Item create | **Done** 191 S5-only Items `STO-ITEM-2026-23482`–`23672` after S6; 5 TESTING/NULL skipped. |
| Orphan Barcode2 | **Done** 1/1. |
| Catalog duplicates | 6 pairs, none merged. |
| Prices + opening stock | **Done** 6,330 PKR prices; 4,261 SLE=Bin; value ₨6,875,960.20; 0 blank dimensions. `MAT-STE-2026-00313`–`00334`. |
| Vendor JEs | **Done** 74 `LEGACY-OB-S5-*`. Held: TEST SUPPLIER; sister `1004` ₨-801,222.31. |
| POS | **Done** cash `1115`; profiles `S5 Counter 1`, `S5 FP1` (renamed from Food Panda), `S5 HD` (Home Delivery, selling list). Cashiers `nafees@`, `fps5@`, `hds5@` (POS User + S5 Branch permission). |
| Foodpanda prices | **Done** 9,883 PKR prices on `S5 - Sector C Foodpanda Price List` only (`import_s5_foodpanda_prices.py`). |
| FBR | **Enabled** by user: `branch_code=1005`, `pos_id=190759` (unique vs S1/S4/S6/S7). |

## File roles (do not mix)

| File | Use for | Do not use for |
|---|---|---|
| `Ho-MasterItemFilea16c3b.xlsx` | Create missing Items | S5 qty / S5 selling price as sole source |
| `1005stockposition.xls` | S5 stock + S5 selling prices | Inventing Item Group / FBR / Brands |
| `1005Vendorbalances.xlsx` | Opening JEs `LEGACY-OB-S5-*` | Item create |
| `productsS5.xlsx` | S5 Foodpanda prices | Selling prices / stock |

Crystal garbles the stock-file header; parse seven columns positionally.

## Hard gates (same as S4/S7/S6)

1. Existing Item Groups only (S4 aliases including Fruits & Vegetables).
2. No new FBR Tax Category. No new Brand.
3. PKR only. Do not run `lock_pkr_currency.py` on live.
4. Dry-run default. Live apply needs backup, totals, rollback.
5. Idempotent. Verify Bin/SLE/GL.
6. Series `item_code`. No warehouse on Item create.
7. Stamp branch and cost center on Stock Entry header and every row.
8. Do not post if `S5 - Sector C - SSM` already has nonzero bins without these opening tags.

## Pass order (live `szl`, after S6)

1. Optional second verified backup after S6.
2. `audit_s5_barcodes_vs_szl.py` → `Home/Migrations/S5`.
3. `reconcile_s5_missing_vs_master.py`.
4. `create_s5_missing_items.py` (skips barcodes that exist after S6).
5. `attach_s5_orphan_barcode2.py`.
6. `detect_s5_duplicate_catalog_items.py`.
7. `import_s5_prices_and_stock.py` (`S5-ONHAND-IMPORT-2026-09-20`).
8. `import_s5_vendor_balances.py`.
9. `setup_s5_pos_golive.py` (cash `1115`, `Cash - S5SC`, profiles disabled).
10. `setup_s5_fbr.py`. Enable only after unique `pos_id`.
11. `import_s5_foodpanda_prices.py` — write only `S5 - Sector C Foodpanda Price List`.
12. `enable_s5_s6_pos.py` after unique FBR `pos_id`.

Confirm S1/S4/S6/S7 bins and prices unchanged after stock post.

## Scripts

| Artifact | Role |
|---|---|
| `audit_s5_barcodes_vs_szl.py` | Gap audit |
| `reconcile_s5_missing_vs_master.py` | Read-only master reconcile |
| `create_s5_missing_items.py` | Missing Items from master |
| `attach_s5_orphan_barcode2.py` | Orphan Barcode2 |
| `detect_s5_duplicate_catalog_items.py` | Catalog split report |
| `import_s5_prices_and_stock.py` | PKR prices + opening stock |
| `import_s5_vendor_balances.py` | Vendor opening JEs |
| `setup_s5_pos_golive.py` | Cash / MoP / customers / POS |
| `setup_s5_fbr.py` | Disabled FBR copy (user later set `pos_id=190759` and enabled) |
| `import_s5_foodpanda_prices.py` | PKR Foodpanda prices from `productsS5.xlsx` |
| `enable_s5_s6_pos.py` | Enable S5/S6 profiles after unique FBR IDs |
| `assign_s5_s6_pos_users.py` | Cashiers, FP1 rename, S5 HD (no passwords in file) |
