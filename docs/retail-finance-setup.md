# Retail Finance Setup and Control Framework

Version: 1.0.0
Effective: 2026-07-22
System of record: ERPNext / Aimatic

## Purpose

This is the permanent specification for SIEZAL's retail-finance foundation and management-reporting roadmap. It distinguishes what is working, what is only partly reliable, and what must be implemented separately. The code-owned capability register is `aimatic/retail_finance_setup/registry.py`; this document explains the business contract.

The **Retail Finance Setup** Desk console runs read-only checks and has one deliberately narrow,
idempotent setup action: Accounts Managers/System Managers may initialize missing branch
selling-only Price Lists. It does not create accounts, alter opening entries, backfill Branch
accounting values, or repair transactions.

## Accepted cutover basis

- No complete historical operational dataset is available.
- Existing supplier/vendor, inventory, and accounting opening entries created during iPOS migration are the accepted cutover baseline.
- Reporting and controls proceed forward from that opening date.
- Do not reconstruct unavailable sales, purchase, stock, or payment history.
- Do not modify current `ipos_data_migration` entries as part of this framework.
- Opening Journal Entries without Branch remain part of the accepted baseline. Forward income and expense activity must carry Branch.
- Any later opening-balance wizard is for a future site or branch onboarding; it must never silently rerun against an established company.

## Guided setup sequence for a future site

The future operator should be guided through this order, with a visible owner and completion evidence for each step:

1. Company identity, fiscal year, currency, chart of accounts, receivable/payable controls, and default cost center.
2. One Branch per store, one enabled leaf cost center per store, finished/rejected leaf warehouses,
   the Branch Accounting Dimension, and one enabled selling-only `<Branch> Selling Price List`.
3. POS Profiles and terminals mapped to the exact Branch, warehouse, and cost center; Modes of Payment mapped to company cash/bank accounts.
4. Stock valuation policy, items, opening quantities and values, supplier/customer opening balances, and a balanced opening trial balance.
5. Tax applicability, FBR settings, tax accounts, invoice sequence, and filing ownership.
6. Cashier opening/closing, deposit ownership, bank statement import, petty cash, approval limits, and period-close cadence.
7. Readiness report reviewed before go-live; blocking failures resolved, warnings accepted with owner/date, and evidence retained.

Opening quantities, values, and balances must come from an approved cutover pack. A user-facing wizard may validate and stage them, but ERPNext remains authoritative and final posting requires normal permissions and balanced accounting.

## Capability coverage

### Working or standard ERPNext foundation

- Company/chart of accounts and control accounts
- Branch, warehouse, cost-center, and Branch-dimension structure
- POS/cashier accounting context and cashier closing
- Store-wise P&L and branch expense attribution where forward GL entries carry Branch
- Company and consolidated P&L/financial statements when the required company tree exists
- Inventory valuation, supplier payables, customer receivables, bank reconciliation, and budgets
- Cash/bank balances, tax ledgers, payment summaries, order/return reporting, and vendor performance

“Standard” means ERPNext supports the function; it does not mean SIEZAL has completed every operating procedure or that multi-company/multi-store results have been proven with live data.

### Partial; requires control or certification work

- Opening-balance onboarding: current openings accepted, reusable guided future workflow not yet built
- Store cash position and daily cash/bank position
- Gross profit by category and store gross-margin percentage
- Inter-store stock/accounting reconciliation
- Void/refund financial-impact reconciliation
- VAT/sales-tax filing reconciliation dashboard
- Forward Branch completeness: monitored, but warnings must be resolved prospectively

### Separate implementation backlog

- Store-wise balance sheet with approved asset/liability attribution
- Daily sales vs deposit-batch vs bank reconciliation and variance ageing
- Petty-cash issue, evidence, replenishment, count, and variance workflow
- Head-office cost allocation with versioned drivers
- Physical shrinkage, wastage, expiry, theft, and stock-adjustment classifications
- Supplier rebate agreements, accruals, claims, and credit-note matching
- Certified branch EBITDA
- Stock Ledger to inventory GL reconciliation
- POS sales/tenders/tax/returns to GL reconciliation
- Supplier/customer subledger to control-account reconciliation

These items remain registered even before development. A missing feature must be labelled **planned/separate**, never represented by a dummy action or a button that does not perform the stated operation.

## Readiness rules

A company is ready for forward operations only when critical foundation checks have no blocking result. Current checks cover:

- Company defaults and active leaf chart of accounts
- Branch-to-cost-center-to-warehouse mappings, the Branch Accounting Dimension, and a valid
  selling-only Price List for every Branch
- Enabled POS Profile Branch/warehouse/cost-center mappings
- Cash, bank, and Mode of Payment account availability
- Receivable and payable control accounts
- Stock items, Branch-mapped leaf warehouses, and valuation method
- Tax/FBR setup as an applicability warning or pass
- Current-fiscal-year Income/Expense GL rows missing Branch

Warnings do not repair data. Planned features do not block basic forward operations unless they become a formally approved go-live requirement.

## Future-change control

Every finance feature or setup-contract change must:

1. Add or update one capability in `registry.py` and increment that capability's version.
2. Increment the registry version for a material release.
3. State `implemented`, `standard`, `partial`, or `separate` accurately.
4. Add/update a read-only check when the requirement can be tested safely.
5. Add automated tests for calculations and button actions.
6. Update this document, `docs/README.md`, and root `CLAUDE.md` in the same change.
7. Preserve the accepted opening-balance boundary unless a separately approved migration explicitly changes it.

Definition of done requires a real route/action, permission enforcement, relevant test evidence, and deployment to the intended sites. UI presence alone is not completion.

## Access and safety

- The readiness API is restricted to Accounts User, Accounts Manager, and System Manager roles.
- Checks are read-only and company-scoped.
- ERPNext remains the source of truth for accounting, stock, tax, and permissions.
- No client or console may write financial values from browser calculations.
- Corrections use normal ERPNext documents and approval procedures; never update submitted ledgers directly.
