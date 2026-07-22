# Aimatic — Application Guide

Aimatic is the shared backend behind a portfolio of retail-tech products — multi-branch
point-of-sale, distributor/sales-rep order booking, customer online shopping, and restaurant
table service — plus everything underneath them: purchasing, pricing, Pakistan FBR
e-invoicing, barcode/shelf labels, loyalty and gift vouchers, supplier management, and vendor
performance reporting, all built on one shared Branch → Warehouse → Cost Center structure. See
"The product portfolio" below for how the pieces fit together.

This guide explains **what the app does and why**, in plain language, so that business
owners, implementers setting up a new site or branch, and developers new to the codebase can
all get oriented without reading source code. For the technical "how it's wired" detail
(hooks, doctypes, gotchas learned the hard way), see `CLAUDE.md` at the root of this bench —
this guide and that file are meant to be read together, not as duplicates of each other.

The customer storefront for the `siezal` site is served at `https://shop.aimatic.tech/`. System Managers can use `https://shop.aimatic.tech/uploadimageproduct` to upload a Shopping Product photo, run local background removal, compare the original and transparent result, and approve the public image. Processing uses an isolated `rembg`/U²-Netp Python 3.12 environment under `/home/nabeel/.local/share/aimatic-bgremove`; it has no external paid API and must not be installed into the Frappe environment.

## The product portfolio

Aimatic isn't a single point-of-sale app — it's the shared backend for a portfolio of four
focused, customer-facing products, all backed by the same business rules, the same
stock/pricing/tax engine, and the same Branch → Warehouse → Cost Center structure:

| Product | Who uses it | What it does | Status |
|---|---|---|---|
| **Retail POS** | Store cashiers, branch supervisors | Ring up sales at the counter — desktop and Android, offline-capable. | **Current focus** — live in production across all three sites, actively developed. |
| **Distributor / sales ordering** | Sales representatives / field staff | Take orders from customers out in the field and place draft Sales Orders against real stock and pricing. | Built (backend + Android app exist and work) — not the current focus of active development. |
| **Online shopping (e-commerce)** | End customers | Browse an enabled product catalogue and check out for pickup or cash-on-delivery. | Built (backend + Android/web app exist and work, storefront is live) — not the current focus of active development. |
| **Restaurant management** | Waiters, kitchen staff, restaurant managers | Table-side ordering, kitchen ticket dispatch, and bill requests for a restaurant floor. | Built (backend + Android app exist and work) — not the current focus of active development. |

The other three products aren't hypothetical or unfinished scaffolding — they have real, working
implementations (see their own sections below). But **Retail is where active development effort
is currently concentrated**; the other three are a foundation to build on when that work resumes,
not something currently being iterated on. Don't read the table above as "four equally live
priorities" — it's one active product plus three already-built ones waiting for their turn.

**Why one backend, four apps — the architecture principle behind everything in this codebase:**
every product talks to the same ERPNext/aimatic backend as the single source of truth for
prices, stock, taxes, credit, and permissions. Each product gets its own focused client app —
a separate, isolated build with its own login and its own restricted API surface — but never
its own copy of business logic. A price, a tax rule, or a stock check is computed once, in one
place, and every product simply asks the backend for the answer rather than recalculating it
independently. This is deliberate, not an accident of how the code grew: it means a pricing or
tax-compliance fix made once protects every product at once, and it's why each client app's API
surface is scoped to "only what that product needs" (see each product's own section below)
rather than one shared, do-everything API.

**What this means for future development:** a new feature, or a new product line, should extend
this same shape — add the business logic once to the shared backend, then expose only the
minimum surface a client actually needs. Duplicating logic into a client app, or letting one
product's client trust its own calculation over the backend's, breaks the guarantee that every
product agrees on price, stock, and tax at all times.

For the live, numbers-driven view across the portfolio — today's sales by branch, vendor
performance, gross margin — use the Sales Dashboard and Vendor Performance tools described
below; this document describes what the system *is*, not a snapshot of what it's currently
doing.

## Who this guide is for

- **Business owners / operations staff** — read the feature sections below to understand what
  the system does for your branches, cashiers, and suppliers.
- **Implementers** setting up a new site or onboarding a new branch — read the feature
  sections plus "Setting up a new branch" below.
- **Developers** — read this for the big picture, then go to `CLAUDE.md` for exact file paths,
  hook wiring, and the specific technical gotchas that have already bitten this codebase once.

---

## What the system covers

### Branches, warehouses and cost centers

Every branch (physical store location) has its own warehouse and its own cost center — there
is no shared/generic warehouse or cost center anywhere in the system. This means every sale,
purchase, and stock movement is automatically attributed to the correct branch's books without
staff having to pick the right warehouse or cost center themselves — the system fills these in
based on which branch the logged-in user belongs to. Managers (Sales/Purchase/Stock/Accounts/
System Managers) can still override this when genuinely needed, e.g. moving stock between
branches.

### Point of Sale (POS terminals)

Stores use an offline-capable POS application that ships as an Electron desktop app and a
Capacitor Android app. Both use ERPNext as the source of truth and can continue queuing sales
when connectivity drops, but they deliberately authenticate differently:

- **Desktop terminals** retain their machine credentials in the background; cashiers still log
  in individually to open a till, ring up sales, process permitted refunds, and close shifts.
- **Android terminals** never ask for or store an ERPNext API key/API secret. A supervisor first
  opens its POS Profile and generates a short-lived, one-time QR code. The Android enrollment
  screen scans it directly, with copy/paste available as a fallback. Cashiers then sign
  in through ERPNext in the system browser. Enrollment also issues a random device proof whose
  hash alone is kept on the server; the proof and session material are kept in Android's protected
  credential storage. The proof is not an ERPNext API credential and cannot sign in a cashier.
  Every Android token and API request must prove the device is still enabled, so disabling it
  blocks its next online call and signs it out without changing Electron terminals.
- **Administrators/Supervisors** use a separate, more secure step-up login (valid for only
  5 minutes) to perform sensitive actions like resetting a cashier's PIN — this login requires
  a secure (HTTPS) connection and is fully logged for audit purposes.

Every sale and refund is recorded against the correct branch and warehouse automatically.
Stock and accounting only update once a cashier's shift is formally closed out at day's end —
not the instant a sale happens — this is expected behavior, not a delay or a bug.

On Android, the large synced item/barcode catalogue is stored separately from the small live
cart and queue state. Existing installs migrate this automatically. This keeps barcode scans
responsive because adding one item no longer rewrites the complete catalogue to disk.

### Mobile sales ordering

Sales representatives use the separate Ai Matic Sales Android build. It signs each employee in
through OAuth2 PKCE without an API key or secret. Standard ERPNext Company, Warehouse, Stock
Settings, and Selling Settings defaults work even when no Branch is configured. Where Ai Matic
Branch management is configured, ordinary users remain restricted to their assigned Branch and
Branch defaults are used as a fallback. The app shows customer balance/credit, customer pricing,
and warehouse stock and creates normal draft Sales Orders. Offline drafts retain one stable request ID; the server
maps that ID to a single Sales Order so retries cannot create duplicates. Prices, taxes, stock
policy, credit validation, permissions, company, and warehouse remain controlled by ERPNext.

Items can be found by code, name, category, or brand. When an Item has alternate units with valid
conversion factors, the salesperson can choose the unit before adding it and sees the package
conversion, matching price, and available stock. Unconfigured or invalid alternate units stay hidden.
If several warehouses are permitted and no ERPNext default is configured, login returns the
available list and the salesperson must choose one before selecting a customer; the server does
not guess which warehouse should supply the order.

### Customer online shopping

The separate Ai Matic Shopping Android/web build exposes only products explicitly enabled in
`Shopping Product`, using the public Branch and Price List in `Shopping Settings`. Customers use
a separate OAuth2 PKCE Website User linked to their Customer record. Stores may enable safe
self-registration with a Customer Group and Territory: a signed-in Website User can create a new
Customer for themselves, but the system never takes over an existing customer merely because an
email, mobile number, or name matches. The browser build is accepted only from the exact HTTPS
callback configured by the implementer. The first checkout mode is Cash on Delivery with Store
Pickup. ERPNext recalculates a short-lived signed quote, checks stock and prices again at checkout,
and creates one Sales Order per stable request ID. Customer APIs never return internal users,
costs, warehouses, buying/accounts data, suppliers, or reports.

### Restaurant table service

The separate Ai Matic Restaurant Android app gives waiters and kitchen staff a focused ordering
flow for a restaurant floor. A Restaurant Profile ties one Branch, Company, and POS Profile
together with its own floors, tables, menu items, and modifier groups; ERPNext remains the
source of truth for prices, stock, taxes, customers, and the submitted POS Invoice — Restaurant
Orders and kitchen tickets themselves don't post any accounting entries. Waiters sign in through
the same secure login pattern as the other apps, see only the tables and orders they're
permitted to use, open and update orders, and send kitchen tickets that can't be accidentally
duplicated even if a request is retried after a dropped connection. Kitchen staff advance those
tickets through a simple status flow and have no access to the wider back-office system beyond
that. Table transfers, bill splitting, and a few other advanced flows are intentionally not
built yet. See [`docs/restaurant.md`](restaurant.md) for the full setup order, role model, and
troubleshooting guidance.

### Purchasing and receiving stock

When a delivery arrives from a supplier, it's recorded as a Purchase Receipt (and later billed
via a Purchase Invoice). The system captures the cost, applicable taxes, and — as of this
release — the intended shelf/selling price and MRP right there on the receipt, so pricing
decisions happen at the point of receiving stock rather than as a separate step later.

### Selling prices: branch pricing, MRP, and Foodpanda

Every branch can now have its own selling price for the same item — useful when one branch's
market conditions call for a different retail price than another's. When a Purchase Receipt is
submitted, the person submitting it is asked two quick yes/no questions:

1. *"Update this branch's selling price for these items?"* — if yes, the price and MRP
   entered on the receipt are pushed to that branch's own price list (created automatically
   the first time it's needed) and the item's MRP is updated everywhere.
2. *"Update the Foodpanda price for these items?"* — answered independently of the first
   question, since a store may sell in-branch and on Foodpanda on different schedules. For now
   the Foodpanda price is simply set equal to the item's MRP; a markup/commission formula can
   be added later if the business needs one.

If either question is skipped, or the person submitting the receipt doesn't have permission to
update prices (see Roles below), nothing is silently lost — the receipt shows a clear
"Pending" status with a button to apply the update later once the right person reviews it.
Every price change is logged (old price, new price, who, when, which receipt) so it can always
be traced back, and cancelling a receipt safely undoes its own price change — but only if
nothing more recent has already overwritten it, so an old correction can never accidentally
clobber a newer price.

### FBR e-invoicing (Pakistan tax compliance)

Every POS sale is automatically reported to Pakistan's Federal Board of Revenue (FBR) as
required by law, with the correct tax category and rate calculated per item. Refunds are
reported to FBR independently of the original sale, so a return is never mistaken for a
duplicate of the original transaction.

### Barcode and shelf-label printing

Staff can print barcode labels or A4 shelf-price labels directly from a submitted Purchase
Receipt, Delivery Note, Sales Invoice, or stock transfer — with configurable label templates
(size, columns, fonts, which fields appear) so different label types (small barcode stickers
vs. full shelf-price cards) can be maintained without any code changes.

The print layouts themselves — barcode labels, shelf labels, the purchase receipt/invoice print
layouts, and the POS receipt layouts — ship automatically with the app. An implementer setting
up a new site does not need to manually recreate any of these; they're already there the moment
the app is installed.

### Customer loyalty points and gift vouchers

Customers earn loyalty points on every purchase, at a rate that can be configured per item
category (so, for example, low-margin staples can earn at a different rate than high-margin
goods). Gift vouchers are automatically issued when a sale crosses a configured spending
threshold, and can be redeemed by code on a future visit without distorting the tax-reported
sale value.

### Supplier management and FBR NTN verification

Supplier records track each vendor's National Tax Number (NTN) and withholding-tax group
(Filers vs. Non-Filers), and staff can verify a supplier's FBR registration status directly
from the Supplier record with one button click — showing registration status, registration
type, and whether the supplier is on FBR's Active Taxpayer List, without needing to check the
FBR portal separately.

### Vendor performance reporting

A dedicated Vendor Performance page gives finance and leadership a fast, drill-down view of
any supplier: how much of their stock is still on the shelf, recent sales and gross margin on
their items, outstanding payables, and an early-warning signal for when an item is selling
slower than expected after a delivery (which can indicate a supplier under-delivering versus
what was recorded as received). Every figure can optionally be narrowed down to one branch or
warehouse.

### AI business intelligence assistant

Managers can ask business questions in plain language from the AI Assistant page and receive
live KPI cards, charts, sortable tables, source details, and follow-up suggestions instead of
manually assembling reports. It covers sales trends, margins and below-cost pricing, purchasing
and supplier concentration, stock aging and reorder recommendations, customer activity,
payables/receivables, cash, profit and loss, tax balances, and operational exceptions such as
stale drafts or long-open shifts.

For questions outside those certified metrics, the assistant can run an existing permitted
ERPNext report or combine approved business measures and dimensions (for example, net sales by
customer group this month versus last month), then drill down to the real documents behind a
figure. Every path is read-only, locked to the user's company and branch visibility, capped to a
safe result size, and limited to server-approved reports, datasets, measures, dimensions, and
filters — it cannot invent SQL or create, submit, or modify ERP records. Answers can be saved,
pinned to a personal dashboard, exported, scheduled by email, or used for alert rules. Dashboard
widgets are saved snapshots, so opening or reloading a dashboard does not spend AI requests or
silently change its figures. Use **Refresh Dashboard** when current figures are needed; it asks for
confirmation, refreshes the widgets one at a time, shows progress, and keeps the previous snapshot
for any widget whose AI refresh fails or returns no structured data.

### Legacy data migration

When bringing a new store onto this system from the old "iPOS" software, item and supplier
data (barcodes, pricing, stock levels, vendor ledgers) is imported using a documented,
repeatable process rather than manual re-entry — see `ipos_data_migration/` for the runbooks
and per-site scripts used for this.

---

## Roles, in plain terms

- **Store/POS staff** — ring up sales, process refunds if permitted, submit Purchase Receipts.
- **Buying Price Control** — the only role (besides System Manager) that can actually apply a
  branch or Foodpanda selling-price update from a Purchase Receipt. Store staff can still
  submit the receipt and answer the pricing questions without this role — the update is just
  queued as "Pending" for someone who holds it to apply later.
- **Branch/Sales/Purchase/Stock/Accounts Managers, System Manager** — can override the
  branch/warehouse/cost-center a document would otherwise auto-fill, for the rare cases that
  genuinely need it (e.g. inter-branch stock transfers).
- **POS Supervisor** — can process refunds and close a shift on the POS terminal, in addition
  to normal cashier actions.
- **Restaurant Waiter** — signs in to the separate Restaurant Android app, sees only permitted
  Restaurant Profiles/tables, opens table orders, sends idempotent kitchen tickets, and requests bills.
- **Kitchen User** — updates queued kitchen tickets through the restricted Restaurant service;
  it does not grant general ERPNext Desk access.
- **Restaurant Manager** — configures Restaurant Profiles, floors, tables, menu items/modifiers,
  and can supervise restaurant orders and kitchen status.

## Setting up a new branch or site

At a high level, bringing a new branch or site online involves:

1. Creating the Branch record (its own warehouse and cost center are created alongside it).
2. Assigning staff to that branch (via a default Branch permission) so their sales/purchases
   post to the right books automatically.
3. Setting up POS Profile(s) and terminal(s) for that branch.
   Android terminals additionally require a supervisor-generated one-time enrollment QR; the
   public OAuth client is installed automatically by the Aimatic migration.
4. If migrating from the old iPOS software, running the item and supplier import scripts in
   `ipos_data_migration/` for that site.
5. The first time a Purchase Receipt for that branch applies a branch price update, its own
   Selling Price List is created and populated automatically — no separate setup step needed.

For Restaurant service, also create one enabled Restaurant Profile mapped to the Branch,
Company and POS Profile, then add floors, tables, Restaurant Menu Items and any modifier groups.
The mapped POS Profile supplies the default customer, warehouse and selling Price List unless
the Restaurant Profile explicitly overrides them. ERPNext stock, prices and the submitted POS
Invoice remain authoritative; Restaurant Orders and Kitchen Tickets do not post accounting entries.
See [`docs/restaurant.md`](restaurant.md) for the full setup order, role model, DocType map,
API contract, troubleshooting guidance and deferred integration scope.

For the exact technical steps (custom fields, patches, nginx/HTTPS configuration, etc.), see
`CLAUDE.md`.

---

## Keeping this guide current

Like `CLAUDE.md`, this guide must be kept up to date as the app evolves — but from a
different angle. When you (or an AI assistant working in this repo) add or change a
user-facing feature:

- Update **this guide** (`docs/README.md`) with what changed *from a business/implementer
  point of view* — what a store manager, cashier, or implementer would notice or need to do
  differently. Plain language, no code.
- Update **`CLAUDE.md`** with what changed *from a developer's point of view* — the technical
  wiring, gotchas, and exact file/field/function names a future engineer would need.

Both updates belong in the same session as the change itself, not deferred to later. The
top-level `apps/aimatic/README.md` stays generic (installation/contributing/CI) and just
points here — feature explanations belong in this file, not there.
