# Legacy Customer + Loyalty Points Import Mapping To ERPNext

Companion to `import.md` (items) and `supplierimport.md` (suppliers). Covers importing a
legacy customer/loyalty-points workbook (e.g. `customerdatavip.xlsx`, S1/Ghouri Town VIP
customers) into ERPNext `Customer` + `Loyalty Point Entry`. Script:
`import_szl_customers.py` (same `bench console` / `exec(open(...).read(), globals())`
convention as the other scripts here — plain `exec(open(...).read())` with no explicit
`globals()` fails with `NameError` on top-level constants inside IPython's `bench console`).

## Source shape

`CustomerCode, CustomerName, MobileNo, LoyaltyPoints` (one sheet, no header variants seen).
`MobileNo` is always a 12-digit `92XXXXXXXXXX` string in the source (no other formats
observed on the szl VIP file).

## Field mapping / decisions

- **MobileNo is the Customer PK** — enforced independently by
  `offline_pos.customer_validation.validate_customer` (Customer `validate` hook), which
  normalizes to `+92XXXXXXXXXX` and throws on a duplicate. The import script normalizes
  with the same `normalize_pak_mobile` function (imported directly, not reimplemented) so
  its own pre-merge grouping key matches exactly what the hook would produce.
- **Legacy CustomerCode dedup by MobileNo** (2026-08-02 decision, same shape as the
  Supplier NTN-merge convention in `supplierimport.md`): a handful of legacy rows share one
  real person's number under a different code/name spelling. These merge into one Customer:
  `LoyaltyPoints` **summed** across the group, every legacy `CustomerCode` kept (comma-joined
  into the new `Customer.custom_legacy_customer_code` field — not fixture-tracked before this
  import; captured into `fixtures/custom_field.json` in the same session). The winning row for
  the merged Customer's *name* is whichever member has the highest `|LoyaltyPoints|` (no
  debit/credit activity signal exists for customers, so points magnitude is the closest
  available proxy to the supplier script's `|debit|+|credit|` rule).
- `customer_type` = `"Individual"`, `customer_group` = `"Individual"`, `territory` =
  `"Pakistan"` — same convention already used by `offline_pos.api.create_walkin_customer`.
  Neither is schema-mandatory but both were left unset nowhere else in the app, so these are
  an explicit, documented default rather than a guess.
- `Customer.loyalty_program` is set explicitly on every imported Customer to whichever
  Loyalty Program the run creates/finds (see below) — relying on `auto_opt_in` alone was not
  tested and explicit is unambiguous.

## Loyalty Program prerequisite

No `Loyalty Program` record existed on **any** site (szl/siezal/hsm) before this import —
the `aimatic.loyalty` feature (item-group-weighted earning rate correction, see the
`loyalty-gift-voucher` skill) had been built but never actually activated anywhere.
`import_szl_customers.py`'s `ensure_loyalty_program()` creates one if missing
(`LOYALTY_PROGRAM_NAME`/`LOYALTY_CONVERSION_FACTOR`/`LOYALTY_EXPIRY_DAYS` constants at the
top of the script). Confirmed for szl (2026-08-02): **"Siezal Loyalty Program"**, single
tier, 1 point = Rs 1, 365-day expiry, `auto_opt_in=1`. The single required `collection_rules`
row (`min_spent` must be exactly `0` for the lowest/only tier — core's own
`validate_lowest_tier` throws otherwise) is a flat placeholder factor; it is never actually
used to compute a real sale's points, since `aimatic.loyalty.events` overwrites core's
just-created `Loyalty Point Entry` in place with the item-group-weighted total immediately
after submit.

**Separately still true after this import**: no Item Group has
`custom_loyalty_rate_configured` checked on any site, so no new POS sale earns any loyalty
points yet — that's a per-Item-Group business decision, deliberately left for later
(2026-08-02 explicit scope decision), not part of this import.

## Opening Loyalty Point Entry

One `Loyalty Point Entry` per merged Customer with a nonzero summed legacy balance (customers
with zero legacy points are skipped, not given an empty entry). `loyalty_points` is an `Int`
field, so fractional legacy sums (e.g. `137.21`) are rounded to the nearest whole point —
every rounding event is printed to console during the run for audit (net drift on the
szl run: 276,307 imported vs 276,291.34 source, i.e. +15.66 from rounding, all upward-biased
roundings outweighing downward ones — expected, not a bug). `invoice_type` is set to the
literal string `"Journal Entry"` as a placeholder marker (there is no real source invoice
for an opening balance); `invoice` is left blank. `discretionary_reason` carries the legacy
CustomerCode(s) for traceability. Idempotency check: `frappe.db.exists("Loyalty Point Entry",
{"customer": ..., "loyalty_program": ..., "invoice_type": "Journal Entry"})`.

## Gotcha hit during the szl run (2026-08-02): default_price_list

`offline_pos.customer_validation.set_default_price_list_if_missing` (called from
`validate_customer` on every Customer save) fills a blank `default_price_list` from
`Selling Settings.selling_price_list` — a single **site-wide, non-branch-scoped** default.
On szl this was set to `S2 - Bhatta Chowk Selling Price List` (unrelated branch), so all
4,458 imported customers (an S1/Ghouri Town VIP-only file) silently got S2's price list.
Fixed by bulk-correcting every affected Customer to `S1 - Ghouri Town VIP Selling Price
List` and repointing `Selling Settings.selling_price_list` itself to S1 (today's actual
go-live branch). **This is a recurring risk, not just a one-time fix**: `Selling Settings`
has exactly one global `selling_price_list` value for the whole site, so it will keep
silently mis-defaulting any future Customer created without an explicit
`default_price_list` (walk-in customers from other branches' POS terminals included) unless
it's revisited every time the "current active branch" changes, or a branch-aware default
mechanism is built for it later (none exists today — same class of gap as the `Item Group`
loyalty-rate cascade, but nothing branch-aware backs this one at all).

## Run order / idempotency

Same shape as the other scripts here: `DRY_RUN = True` by default (prints parsed row/group
counts and every merge group, writes nothing); set `DRY_RUN = False` for the real pass. Every
phase is guarded by `frappe.db.exists()`/`frappe.db.get_value()` checks, so an interrupted or
re-run pass resumes rather than duplicating Customers or Loyalty Point Entries — this was
exercised for real on szl (the run was killed by a shell timeout partway through Phase 1;
re-running picked up cleanly from 3,304/4,458 customers already created, and a second bug fix
to Phase 2's customer lookup was similarly safe to re-run with zero duplicate entries created).
