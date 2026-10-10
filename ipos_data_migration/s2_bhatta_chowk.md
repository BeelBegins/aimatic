# S2 Bhatta Chowk migration

Branch go-live data for **S2 - Bhatta Chowk** on company Siezal Supermarket
(SSM), live site `szl`, done 2026-10-11 directly on live (no `siezal` mock).
Same hard gates as `s6_khalid_block.md`. Do not re-run any apply below.

Branch, warehouse (`S2 - Bhatta Chowk - SSM`), cost center, Selling and
Foodpanda Price Lists already existed. Ledger suffix not needed: **no cash
account** for S2 (user decision). POS profile and FBR setup are done by the
user, not by these scripts.

## Source files (`sites/szl/private/files/`)

| File | Use |
|---|---|
| `s2ItemMasterho.xlsx` (SHA256 `9d5292e5…`, 41,552 rows) | **One file**: item master + S2 stock + prices (`ItemCode`=barcode 1, `RefCode`=barcode 2, `Onhand`, tax-INCLUSIVE `CurCost`, `Slprice`, `MRP`, `rp`, `Fbr_Tax_Category`) |
| `s2Vendorbalances.xlsx` | Vendor opening JEs |
| `S2CustomerData.xlsx` | Customers + loyalty points only |
| `s2foodpandaprices.xlsx` (File `31def5dd24`, SHA256 `061a6076…`, 14,937 rows) | S2 Foodpanda prices |

## User decisions (these differ from other branches)

- `CurCost` is tax-inclusive → reverse-calculate with the **szl Item's own** FBR
  category (the file's category only creates new Items). `Fbr_Tax_Category` is
  the tax source; `Tax_Rate` is only used for the rp rule.
- Stock is `Onhand` only; **positive and negative both** posted. Rows with
  `Onhand = 0` are ignored entirely (no Item created, no stock).
- Missing Items created **only** if `Onhand != 0` and `SubCatName` is not
  junk (blank/NULL/TESTING). Aliases: COLOR COSMATICS→Colour Cosmetics,
  ELECTONIC ITEMS→Electronic Items, HOUSEHOLD SUNDRIES and WALL CLOCKS→
  Household Essentials, BAGS→Ladies Bag.
- `custom_mrp`: where `rp` exists, **rp wins**: `rp + rp × Tax_Rate/100` (item's
  own 18/25, not a fixed 1.18). Only 3rd Schedule items carry `rp`.
- Selling prices: every resolved Item with `Slprice <> 0`.
- GST backed out of CurCost is booked as an Opening Entry (S1/S7 way).
- Customers: loyalty points only (no balance); rows with no mobile are separate
  Customers keyed by `S2:<code>`; existing mobile → points added; new
  Customers get `default_price_list = S2 - Bhatta Chowk Selling Price List`;
  negative points kept as-is. `Selling Settings` is never touched (site-wide,
  live S1).
- Vendors: sister-store codes `1122`=S1 (Khanna Pull), `1400`=S4 (Bahria PH7),
  `1404`=S3 (DHA PH 1), `1433`=S5, `1449`=S6, `1454`=S7. NTN `3653488` rows
  (1105/1201/1335) all go to `UNIQUE MARKETING SERVICES (EZIGRIP)` (the NTN is
  shared by two szl records; name base and principal decide the rest).

## What was posted (all backups verified; restore only as a last resort)

| Step | Script | Result | Backup |
|---|---|---|---|
| Cancel 9 Material Transfers touching S2 (stock to zero first) | (manual) | `MAT-STE-2026-00350…00440` cancelled | `20261011_003942` |
| Items | `create_s2_missing_items.py` | **5,493** created (`STO-ITEM-2026-23873…29365`) | `20261011_011926` |
| Prices + opening stock | `import_s2_prices_and_stock.py` | 24,025 prices; 15,378 bins (12,941 +ve / 2,437 −ve), excl value ₨34,686,140.64 | `20261011_012801` |
| GST opening | `import_s2_gst_opening.py` | `ACC-JV-2026-01369` ₨5,299,963.91 | same pass |
| Vendors | `import_s2_vendor_balances.py` | 386 JEs `LEGACY-OB-S2-*`, net ₨13,140,371.86; 250 Suppliers created | `20261011_015713` |
| Orphan Barcode2 | `attach_s2_orphan_barcode2.py` | 257 attached | `20261011_020655` |
| Customers | `import_s2_customers.py` | 4,548 Customers, 4,402 loyalty entries (635,213 points) | `20261011_021542` |
| Hold rows | `import_s2_hold_rows.py` | 125 Items stock (`MAT-STE-2026-00547/00548`), 129 prices, 11 barcodes, GST `ACC-JV-2026-01756` ₨88,945.94 | `20261011_030956` |
| S4/S5/S6 GST back-fill | `import_s4_s5_s6_gst_opening.py` | `ACC-JV-2026-01757/58/59` ₨4,079,288.07 | `20261011_032309` |
| Foodpanda prices | `import_s2_foodpanda_prices.py` | 10,145 PKR prices on the S2 Foodpanda list | `20261011_033527` |

Rollback per step: cancel the tagged Stock Entries (`S2-ONHAND-*`) and Journal
Entries (`LEGACY-OB-S2-*`, `S2-ONHAND-*GST-OPENING*`), delete the S2 Item
Prices and the created Items/Customers by creation tag. A full restore would
erase live sales after the backup, so avoid it.

## Held and not posted

- **Hold rows** (barcode conflicts and Items hit by two file rows) were posted
  by name similarity (`import_s2_hold_rows.py`, thresholds in the script).
  27 plainly wrong rows plus `ItemCode 22` were **not** posted: ₨114,069.42
  inclusive, listed in `Home/Migrations/S2/S2_wrong_rows_not_posted.xlsx`.
  Three cost-mismatch merges (`…08042`, `…02406`, `…12632`) are in that list;
  `…05390` (Rasasi, net 0 qty, ₨75.38) was left out.
- `STO-ITEM-2026-10014`: orphan barcode not attached (mixed Box/Pcs barcode UOMs).
- 580 junk-SubCat rows, TEST vendors (`1001`, `1103`), zero-balance vendors.
- Foodpanda: 4,746 unmatched portal rows, 20 ambiguous other-barcode, 3 ambiguous
  barcode 1, 5 conflicting prices skipped.

## Reconciliation (tax-inclusive, file total ₨40,792,497.29)

Main stock ₨39,986,103.26 + hold posted ₨669,257.07 + wrong-list ₨114,024.41 +
`ItemCode 22` ₨45.00 + Rasasi ₨75.38 + junk ₨22,992.17 = file total (±1 paisa).
Qty "mismatches" of a few hundredths are `float_precision` rounding on
fractional-weight Items.

## Gotchas learned

- Item/barcode matching must be **case-insensitive** (`SZ002` vs `sz002` would
  have created a duplicate Item).
- Posted Issue entries value at moving average, so GST must come from the
  stock plan (`inclusive − exclusive`), not from `qty × basic_rate` of posted rows.
- S4, S5 and S6 originally had **no GST opening**; back-filled here. S1 and S7
  had it.
- Saving a barcode-named legacy Item adds its own barcode row with blank UOM
  (69 such rows exist on szl); not a migration defect.
- `Temporary Opening` is **not yet closed**: `close_migration_opening_balance.py`
  assumes one branch and the wrong company name, so it needs a multi-branch rewrite.
