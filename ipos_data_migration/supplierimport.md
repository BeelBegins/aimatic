# Legacy Supplier Import Mapping To ERPNext

This document defines how old-software supplier/vendor data should be mapped into ERPNext.
It is intended to be reusable across `hsm`, `siezal`, and later sites using the same legacy
`schema.xlsx` shape (companion to `apps/aimatic/ipos_data_migration/import.md`, which covers item/barcode/price/
stock import only).

Source workbook shape used for this mapping (single sheet, one row per legacy vendor ledger):

```
SupplierCode, SupplierName, FBRTYPE, ContactPerson, NTNo, StandardNTN, WhtTax%, LedgerCode,
TotalDebit, TotalCredit, ClosingBalance
```

`siezal`'s `vendordataghouritown.xlsx` adds one extra column not in the shape above: `ContactNumber`
(between `ContactPerson` and `NTNo`). See "Contact mapping" below.

## Why this needs a merge step

The legacy system gives one distributor a separate `SupplierCode` per brand/principal it carries —
same company, same NTN, a different ledger row per brand for bookkeeping. ERPNext needs one Supplier
per NTN to get Withholding Tax right (thresholds and FBR withholding statements are calculated per
party); splitting one real vendor across several "different" Suppliers makes that math wrong.

## Import Principles

- Group legacy rows by `StandardNTN` (the pre-normalized NTN column, not the raw `NTNo`, which carries
  dashes/leading zeros — `StandardNTN` is the number to group and dedupe on).
- Rows with a blank `StandardNTN` cannot be deduped by any stable key: each imports as its own
  standalone Supplier, one legacy row per Supplier.
- Within a duplicate-NTN group, the row with the highest `|TotalDebit| + |TotalCredit|` (the most
  active real trading relationship) is the "winning" row: its `SupplierName`, `FBRTYPE`, and
  `WhtTax%` become the merged Supplier's own values. This also resolves the ~17 groups (out of 60,
  as of the `hsm` schema.xlsx snapshot analyzed 2026-07-13) where members disagree on `FBRTYPE` or
  `WhtTax%`.
- **All** legacy `SupplierCode`s in a group are kept, not just the winner's — comma-joined into
  `Supplier.custom_legacy_supplier_code` (e.g. `0008,0095,0134,0425,0725,0742,0786`), so every
  original code stays visible on the merged record.
- `StandardNTN` -> `Supplier.tax_id` (must be unique per Supplier — this is exactly why duplicate
  NTNs must be merged before import, not after).

## Relevant ERPNext Fields

### Supplier
- `supplier_name` (from the winning row)
- `supplier_group` — no legacy column maps to this; defaults to `Distributor` in the import script,
  since nearly every legacy vendor name in the `hsm` sample is a "SomeCo (Brand)" style distributor.
- `tax_id` — from `StandardNTN`, blank for un-mergeable no-NTN rows.
- `custom_legacy_supplier_code` — comma-joined list of every legacy `SupplierCode` in the group.
- `tax_withholding_group` (Link to `Tax Withholding Group`) — only two groups exist:
  `Filers` and `Non-Filers`. From the winning row's `FBRTYPE`, mapped through
  `FBRTYPE_TO_WHT_GROUP` in the script (`FILER`->`Filers`; `NONFILER`/`EXEMPT`/`NO` all ->
  `Non-Filers`), **not** the raw legacy string. `FILER` must land on the group literally named
  `Filers` — `supplier_management/events.py`'s `validate_supplier_ntn` hook checks
  `doc.tax_withholding_group == "Filers"` to enforce the 7-char NTN format; any other spelling
  silently skips that validation for every imported filer. The group-conflict check (previously
  ~4 of 60 NTN groups) is done on this mapped value, not the raw `FBRTYPE`, so e.g. a group mixing
  `NONFILER` and `EXEMPT` members isn't flagged — both resolve to `Non-Filers` either way.
- **No valid `StandardNTN` forces `Non-Filers`, regardless of `FBRTYPE`.** Blank *and* malformed
  count the same way: a `StandardNTN` only becomes a grouping key / `tax_id` if it matches the same
  7-char alphanumeric shape `validate_supplier_ntn` requires (`NTN_PATTERN` in the script). Anything
  else — blank, or present-but-malformed (e.g. `hsm` legacy code `0858`, `StandardNTN = ' 848388'`,
  a leading space and only 6 digits, `FBRTYPE = FILER`) — is treated exactly like "no NTN": its own
  standalone group (never merged with anything, even another row with the same malformed string),
  no `tax_id` stored, forced to `Non-Filers`. Confirmed against the `hsm` run: 77 of 1,031 groups
  were blank-NTN-but-FILER and failed outright before the override was added; one more (`0858`) was
  the malformed-but-nonblank case, handled by the same rule after generalizing it.
- Malformed `StandardNTN` values are explicitly nulled out (not just logically ignored) before the
  grouping step, so the garbage value never ends up persisted anywhere on the merged Supplier. On
  `siezal`, 11 rows have a malformed `StandardNTN`; several look like a `SupplierCode` value that
  leaked into the NTN column (e.g. `StandardNTN = '001'`, which is also a real `SupplierCode`
  elsewhere in the same file) rather than a genuine bad NTN — still handled by the same
  no-NTN/standalone/Non-Filers rule above, just with the field cleared first.
- `tax_withholding_category` (Link to `Tax Withholding Category`) — from the winning row's
  `WhtTax%`, one category auto-created per distinct rate value found. `WhtTax% = 0` -> category
  named `Exempt` rather than `WHT 0%`; any nonzero rate -> `WHT <rate>%` as before, rate applied
  normally. This naming split applies regardless of `Filers`/`Non-Filers` group — both groups can
  have `0%` rows (confirmed on `siezal`: 25 zero-rate Filers, 341 zero-rate Non-Filers), and both
  map to `Exempt`. Each category gets one `Tax Withholding Rate` row
  (`WHT_RATE_FROM_DATE`..`WHT_RATE_TO_DATE` in the script, currently a wide placeholder range) and
  one `Tax Withholding Account` row against a `Withholding Tax Payable - <abbr>` account
  (auto-created under `Duties and Taxes` if it doesn't already exist).

### Opening balances (Journal Entry, one per legacy row, not one per merged Supplier)
- Every legacy row with a nonzero `ClosingBalance` gets its **own** `Opening Entry`-type Journal
  Entry, `is_opening = Yes`, posted against the merged Supplier as `party` — deliberately not one
  combined entry per NTN group, so that opening a merged Supplier's ledger still shows exactly which
  original legacy vendor/brand contributed which amount (via each row's `user_remark`:
  `Legacy vendor <SupplierName> (Code <SupplierCode>, Ledger <LedgerCode>, NTN <StandardNTN>)`).
- Sign convention (verified against sample rows): `ClosingBalance = TotalCredit - TotalDebit`.
  Positive -> we owe the supplier (credit the payable account). Negative -> an advance/overpayment
  to the supplier (debit the payable account). Balanced against `Temporary Opening - <abbr>`.
- Idempotent on rerun: each entry's `cheque_no` is set to `LEGACY-OB-<SupplierCode>`; a rerun skips
  any code that already has a non-cancelled Journal Entry with that reference.
- `ClosingBalance` is rounded to 2dp before the zero-check — `TotalCredit - TotalDebit` can leave a
  float dust remainder (e.g. `1e-9`) that's truthy in Python but rounds to `0.00` at ERPNext's
  currency precision, which then fails the Journal Entry with "Both Debit and Credit values cannot
  be zero" (hit 2 rows on the `hsm` first pass before this fix).
- `Temporary Opening` is shared with the item import's opening-stock entries (see `import.md`'s
  "Opening-stock GL posting" note, fixed 2026-07-23) — both migration halves must wash through this
  one suspense account, closed once via `close_migration_opening_balance.py` after both imports are
  done, rather than opening stock leaking into a P&L account on its own.
- **Branch/Cost Center dimension (fixed 2026-07-23)**: unlike Sales/Purchase/Stock Entry, `Journal
  Entry` has no `aimatic.branch_management` doc_events hook at all — nothing populates
  `Journal Entry Account.branch`/`cost_center` unless this script sets them itself.
  `resolve_company_branch(company)` requires the company to have exactly one Branch (throws
  otherwise, since legacy vendor-ledger rows carry no branch/location column to split on) and stamps
  `branch`+`cost_center` onto every account line of every opening-balance Journal Entry. Confirmed
  live on `siezal` before this fix: `cost_center` on the 1,124 legacy JE lines came out correct only
  by coincidence (Company's own default cost center happened to already equal the site's one
  Branch's cost center), while `branch` was blank on all of them — the same unrecoverable gap the
  root CLAUDE.md's GL Entry branch-backfill note documents for these exact rows.
- `ContactPerson` (only ~2.5% populated in the `hsm` sample) and `LedgerCode` are not persisted as
  their own Supplier fields — `LedgerCode` only appears in the opening-entry remark text for
  traceability; `ContactPerson` was dropped on `hsm` for being too sparse to be worth a dedicated
  mapping. Fill rate is file-specific, not a fixed rule — re-check it per site before reapplying this
  decision (see "Contact mapping" below, where `siezal`'s much higher fill rate changes the call).

### Contact mapping (only when source file has `ContactNumber`)

`siezal`'s `vendordataghouritown.xlsx` has a much higher contact fill rate than the `hsm` sample that
justified dropping `ContactPerson` above (`ContactPerson` 47% populated, `ContactNumber` 35%, vs.
~2.5% on `hsm`). Where both are present at a usable rate:

- `ContactPerson` + `ContactNumber` -> a linked ERPNext **Contact** record (dynamic-linked to the
  Supplier), not a Supplier field directly.
- Sourced from the same winning row that supplies `supplier_name`/`FBRTYPE`/`WhtTax%` for the merged
  Supplier (the highest `|TotalDebit| + |TotalCredit|` row in the NTN group) — contact info is not
  separately merged/deduped across a group's other rows.

## Ignored / Non-Mapped Columns
- `NTNo` — superseded by `StandardNTN` for grouping/`tax_id`; not stored separately.
- `ContactPerson`/`ContactNumber` — dropped on `hsm` (too sparse); mapped to a Contact record on
  `siezal` instead (see "Contact mapping" above) — re-evaluate per site by fill rate, don't assume
  either precedent applies blindly.

## Reference Scripts

Every per-site import script lives in `apps/aimatic/ipos_data_migration/` alongside this doc — not
in any site's `private/files/` — so they stay under version control and the whole migration toolkit
(docs + scripts) is one browsable, git-tracked unit. A script's `FILE_PATH` constant still points at
the source workbook wherever it was uploaded on that site (`sites/<site>/public/files/...` or
`private/files/...`); only the script itself is centralized.

- `import_hsm_suppliers.py` implements both phases (Supplier creation, then opening-balance Journal
  Entries) against `sites/hsm/public/files/schema.xlsx`, following a dependency-free XLSX-parsing
  convention (raw `zipfile`/`ElementTree`, no `openpyxl` — it isn't a declared `aimatic` dependency;
  `import_hsmitems.py` uses the same parser). This is the original/simplest reference implementation.
- `import_siezal_suppliers.py` is the more complete reference — same core logic, plus the
  `Exempt`-category naming, Contact-record mapping, and malformed-NTN nulling documented above (all
  first introduced for `siezal`). Prefer this one as the starting point for a new site's script.

Edit the constants at the top of a script (`FILE_PATH`, `SUPPLIER_GROUP`, `POSTING_DATE`, etc.) per
site/run; `POSTING_DATE` defaults to today and must be set explicitly before a real migration
cutover. **When starting a new site's import, copy the most complete existing script into a new
`import_<site>_suppliers.py` / `import_<site>_items.py` in this same directory** rather than writing
from scratch or leaving a working copy only in a site's private files — and update this doc (and
`import.md`) in the same session if the new site's data surfaces a mapping decision not already
covered here.

## Site-Specific Import Targets

- `siezal` (`vendordataghouritown.xlsx`, Ghouri Town): Company/Branch target is the site's one real
  branch, `Ghouri Town Phase V`; `POSTING_DATE` is the run date, set explicitly rather than left to
  default, matching this doc's existing guidance above.

- `szl` (`import_szl_suppliers.py`) uses the exact linked Ghouri Town workbook at `sites/szl/private/files/vendordataghouritown.xlsx`; its final blank row is a summary row and must be excluded from vendor data. The target has multiple branches, so the script explicitly sets `BRANCH_OVERRIDE = "S1 - Ghouri Town VIP"`; every opening-entry account row receives that branch and its cost center. The cutover run is dated `2026-08-04` for the planned go-live.
