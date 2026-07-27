# szl multi-branch setup (Company/CoA + Branch structure + Internal Branch Supplier)

Sets up the `szl` site under the **same Company** as `siezal` ("Siezal Super Market", abbr SSM),
since both belong to the same legal entity/NTN.

**Correction, 2026-07-27**: this doc originally assumed `siezal` was the live production site for
S1 (Ghouri Town VIP) and `szl` would host the other 5 stores cross-site. That was wrong — `siezal`
is a test/sandbox deployment only; S1 actually still sells on the old iPOS software in real life
(cutover planned for the night of 2026-07-28→29), and **`szl` is the real production site for the
whole business, S1 included**. S1 was therefore added as a 6th branch on szl directly (same
Cost Center/Warehouse pattern as the other 5), not left as a cross-site relationship. S3 (DHA
Phase 1) is confirmed closed/discontinued — no branch was created for it.

Practical effect: the "Internal Branch Supplier" mechanism below, originally built assuming a real
cross-site S1↔S2-S7 boundary, is **superseded for production use** now that S1 has a real branch
on szl — S1↔S2-S7 movement is just a normal same-database Material Transfer/Journal Entry. The
construct stays in place (harmless) for `siezal`'s own test/sandbox use, not deleted. `siezal`/`szl`
remain separate Frappe sites (separate databases) either way — nothing in these scripts reads live
from siezal at runtime; every value needed was captured by direct inspection of siezal's database
and lives in `szl_reference_data.py`.

## Scope of this pass

Done by these scripts:
- Exact replica of siezal's Chart of Accounts (the 4 manually-added accounts + the 42 Indirect
  Expenses leaves that aren't part of ERPNext's standard numbered template — everything else comes
  from the template itself).
- Tax Withholding Group/Category setup (matches siezal's 7 categories).
- Full Branch → Cost Center → Warehouse structure for all 5 new branches, even though they'll go
  live one at a time later.
- The "Internal Branch Supplier" / Inter-Branch Payable mechanism, so an initial stock transfer
  from S1 into a not-yet-live new branch can be recorded correctly.

**Explicitly deferred, not done here** — added per-branch right before that branch's own go-live:
- POS Profiles (the user only wants these where they already exist — Ghouri Town/S1, on siezal).
- Per-branch Mode of Payment masters (e.g. a "Cash - S2BC" style account) and per-branch stock/cash
  ledger sub-accounts — these are tightly coupled to actual POS/cash operation, not needed while a
  branch is purely structural.
- Item/Customer/Supplier catalog import — a separate later step (see `import.md`/
  `supplierimport.md` for that convention once it's szl's turn).
- The **reverse-direction** mechanism: S1 (on siezal) recording transfers *to* S2–S7 needs a
  symmetric setup on `siezal` itself — a separate site these scripts cannot touch, and separate
  later work.

## Run order

This directory is not an importable Python package (no `__init__.py`, by established convention —
scripts here are meant for `bench console`, not `bench execute <dotted.path>`; `aimatic.` is a
real installed app but `aimatic.ipos_data_migration` is not a real Python package path, so
`bench execute` cannot resolve it). Run each phase from `bench --site szl console`:

```python
exec(open("apps/aimatic/ipos_data_migration/setup_szl_company.py").read())
main()
```
```python
exec(open("apps/aimatic/ipos_data_migration/setup_szl_branches.py").read())
main()
```
```python
exec(open("apps/aimatic/ipos_data_migration/setup_szl_internal_branch_supplier.py").read())
main()
```

Each phase depends on the previous one having completed (Phase B needs the Company from Phase A;
Phase C needs all 5 Branches from Phase B). Every script is safe to re-run — every insert is
guarded by a `frappe.db.exists()` check, so a re-run after a partial failure resumes from wherever
it stopped rather than re-creating anything.

Note: unlike `import_siezal_items.py`/`import_siezal_suppliers.py` (which call `run()`
unconditionally at module scope), these three scripts define `main()` without calling it
automatically — call it explicitly after the `exec(open(...).read())` line, as shown above.
(Named `main()`, not `run()`, because `run` collides with IPython's `%run` magic in `bench
console` and gets silently misinterpreted.) Each script loads its reference data from
`szl_reference_data.py` via an inline `exec`/`compile` of that file's absolute path (not a package
import), for the same reason.

## Naming decisions

- **Branch = Cost Center = Warehouse base name**, one string reused across all three per branch
  (simpler than siezal's own S1, where Branch "Ghouri Town VIP" and Warehouse "Ghouri Town Phase V"
  are different strings):
  - S2 → `S2 - Bhatta Chowk`
  - S4 → `S4 - Wallayat Complex`
  - S5 → `S5 - Sector C`
  - S6 → `S6 - Khalid Block`
  - S7 → `S7 - Empire Heights`
- **Branch short-code scheme** (locked in now for later use — Mode of Payment, POS terminal IDs —
  even though this pass doesn't create those yet): S2→`S2BC`, S4→`S4WC`, S5→`S5SC`, S6→`S6KB`,
  S7→`S7EH`. Picked to disambiguate S5/S6, which are both "Bahria Town Phase 8", by using the
  specific plaza name instead of the phase number.
- **Head Office Cost Center**: `Head Office - SSM`, a new non-branch Cost Center that
  `Company.cost_center`/`round_off_cost_center`/`depreciation_cost_center` point at — a deliberate
  deviation from siezal's own Company row (which points these at its one branch's Cost Center),
  per the caveat already recorded in the root CLAUDE.md: mixing head-office rounding/depreciation
  into one specific branch's Cost Center pollutes that branch's own P&L once there's more than one
  branch to compare against. This is exactly that situation, addressed from day one instead of
  revisited later.

## The Internal Branch Supplier mechanism

`siezal` and `szl` are separate Frappe sites — a real ERPNext Material Transfer (warehouse-to-
warehouse Stock Entry) is only possible between two warehouses in the *same* site's database. So
any stock or money movement between S1 (siezal) and any of S2–S7 (szl) is booked on szl's side as
if S1 were a special internal vendor:

- Supplier: `Internal Branch - S1 (Ghouri Town VIP)`, group `Internal`.
- A dedicated `Inter-Branch Payable - <code> - SSM` account per branch (`2141`–`2145`, under the
  new `2140 - Inter-Branch Accounts - SSM` group), **not** the normal Trade Creditors account —
  each branch needs its own account for correct per-branch balance-sheet reporting.
- **No default Party Account is set on the Supplier.** Because ERPNext's Party Account default is
  Company-scoped, not branch-scoped, and the same Supplier is used by every branch, every Purchase
  Invoice/Receipt against this Supplier **must explicitly set `credit_to`** to the correct branch's
  own `Inter-Branch Payable - <code> - SSM` account at transaction time. Do not rely on any default
  — there isn't one, on purpose.
- **No purchase tax or Withholding Tax** applies to these transactions (no `tax_withholding_
  category` is set on the Supplier) — this isn't a real vendor relationship.

Example: S1 sends initial stock to S2 before S2 opens.
```
Purchase Receipt / Purchase Invoice on szl
Supplier: Internal Branch - S1 (Ghouri Town VIP)
credit_to: Inter-Branch Payable - S2 - SSM   (set explicitly, every time)

Debit:  Stock In Hand (S2's warehouse)
Credit: Inter-Branch Payable - S2 - SSM
```

When S2 later settles cash/bank with S1 (recorded manually, since this is cross-site — there's no
automatic reconciliation):
```
Journal Entry on szl
Debit:  Inter-Branch Payable - S2 - SSM
Credit: Bank/Cash (S2's account)
```

**Discontinue this construct** for a given branch once/if cross-site tooling or a full
consolidation makes a real Material Transfer possible — reconcile any outstanding balance on that
branch's `Inter-Branch Payable` account to a proper `Due To`/`Due From Branch` account at that time,
per the cutover approach already agreed for this rollout.

## Verification

See the plan's verification section (`/home/nabeel/.claude/plans/nifty-greeting-lark.md`) for the
full set of post-run SQL checks and `retail_finance_setup.checks` calls. In short: after all three
phases, `check_company` and `check_stores` (`aimatic.retail_finance_setup.checks`) should both
return `status: "pass"` for Siezal Super Market on szl.
