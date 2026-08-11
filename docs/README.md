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

The governed AI Assistant architecture, certified formulas, confidence rules, limitations,
example questions, and evaluation corpus are documented in
[`ai-business-intelligence.md`](ai-business-intelligence.md).

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

For the S1 Food Panda counter, a cashier can choose Credit Sale only when using the
dedicated Food Panda POS Profile and the Food Panda customer. The sale is recorded as an
outstanding Food Panda receivable; it does not post directly to the bank. Accounts later
records the monthly payout with a normal Payment Entry from the Food Panda receivable to the
bank account. This is for orders manually billed at the POS counter; the separate Foodpanda
integration is configured independently.


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

Sales Managers can configure optional customer catalogue filters through Mobile Sales Assortment.
Each enabled row selects one Item or one Item Group; group rules include descendants. The restricted
item search resolves the configured union server-side when the client activates the filter. Customers
without enabled rules retain the full permitted sales catalogue. Deploying this DocType requires the
normal `bench migrate` step on each target site.

Sales Managers can also configure each customer's permitted shipping addresses through Mobile Sales
Delivery Location. Every location must point to an enabled ERPNext Address already linked to that
Customer. A location can be the default, allow selected weekdays, carry driver/receiving instructions,
and define a minimum order value. The Sales app selects the default, shows the schedule and instructions,
and warns below the minimum. ERPNext validates the chosen customer/location/date combination again and
sets the standard Sales Order shipping address; stale or unrelated cached locations cannot be submitted.
Leaving all weekdays unchecked means delivery is allowed on any day. This DocType is installed by the
same normal `bench migrate` deployment step.

Item search can include a three-month customer history summary derived from permission-filtered
submitted Sales Orders. Quantities are aggregated by order in stock UOM, returned only after at least
two orders, and converted by the client to the selected valid UOM as an optional draft suggestion.
This history never supplies a rate, stock value, or final order total.

Per-user discount limits are configured by Sales Managers through Mobile Sales Discount Authority.
The restricted API applies the requested percentage to ERPNext's standard Sales Order discount fields
and recalculates the document server-side. A request above the employee's limit requires a reason and
creates one auditable Mobile Sales Discount Approval record for that Sales Order. Sales Managers see
the pending queue in the Sales app and can approve or reject it; rejection requires a comment and
removes the requested discount. Notification Log plus a live Frappe event alerts the requester and
eligible managers while they are connected. A Sales Order `before_submit` hook blocks pending requests,
rejected discounts that were reintroduced, and discounts raised above the approved percentage. These
two DocTypes are installed by the same normal `bench migrate` deployment step.

The restricted mobile Sales API also exposes a bounded, permission-filtered recent submitted-order
feed for Order Again. It returns item codes, quantities, and UOMs only as draft seeds; the client then
re-runs customer context, catalogue/UOM, pricing, stock, tax, and credit validation before creation.
A cached order can prepare an offline draft but cannot become authoritative without ERPNext.

My Orders keeps server actions unambiguous. View fetches and displays the current ERPNext order without editing it. Edit is offered only for Draft Sales Orders; leaving edit mode restores any separate local order instead of overwriting it. Cancel Order is a different, explicitly confirmed action available only on submitted orders when the employee is a Sales Manager and ERPNext grants cancel permission. It uses normal ERPNext cancellation and never treats leaving the editor as a cancellation. Mobile PO references and notes persist in `po_no` and `custom_mobile_sales_notes`, and raw ERPNext statuses are normalized for the mobile filters.

For deliberate sandbox or pilot testing, a System Manager may run `bench --site <site> execute aimatic.mobile_sales.demo_data.execute`. The idempotent command creates only clearly labeled `AIMATIC Demo` customers, brands, items, UOM conversions, stock, orders, discount approval, promotion, delivery rules, assortments, and visits. It is never invoked by install or migrate and must not be run indiscriminately on production sites.

Active ERPNext Pricing Rules and Promotional Schemes appear in the Sales app as offer cards and item
badges after server-side customer, company, warehouse, date, Item/Item Group, and Brand filtering.
They are guidance only: ERPNext still applies the actual rule, free item, discount, tax, and final total
during preview and order creation.

Sales Managers plan field work through Mobile Sales Visit records in the Aimatic workspace. Each visit
belongs to a Company, Customer, and assigned sales employee, with an optional Warehouse, time, address,
route order, planned coordinates, and instructions. The employee's Visits screen supports an optional
nearest-next route view, GPS check-in/check-out, notes, and up to three private photos. Offline events
keep stable request IDs and replay in sequence, so connection loss cannot duplicate a visit action.

New mobile orders capture the customer's signature and the device GPS location. ERPNext stores the PNG
as a private Sales Order attachment and creates one immutable Mobile Sales Order Proof audit record with
the employee, timestamp, coordinates, and accuracy. Editing any acknowledged order detail clears stale
proof in the app. Sales Managers can inspect proof records but ordinary Sales Users cannot browse the
proof table directly.

The Sales Manager Profile includes a week/month dashboard sourced only from submitted, company-scoped
ERPNext Sales Orders and Mobile Sales Visits. Revenue, order count, average order, completed visits,
stock/visit/approval alerts, and team ranking are display-only server aggregates.

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

The New Item screen keeps the standard ERPNext Details layout, including Item Code, Item Name,
Item Group, and Default Unit of Measure, at every Desk width and with the navigation sidebar
open or collapsed. Aimatic extra Item fields are inserted beside the appropriate standard
fields; they do not replace or reorder the complete Item form.

### Selling prices: branch pricing, MRP, and Foodpanda

Every branch can now have its own selling price for the same item — useful when one branch's
market conditions call for a different retail price than another's. When a Purchase Receipt is
submitted, the person submitting it is asked two quick yes/no questions:

1. *"Update this branch's selling price for these items?"* — if yes, the price and MRP
   entered on the receipt are pushed to that branch's own selling-only price list (created and
   baseline-populated automatically when the Branch is initialized) and the item's MRP is updated
   everywhere.
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

The **Branch Price Sheet** report brings the branch's selling price, MRP, Foodpanda price,
stock and current cost into one view. Use the filters at the top to find an item or barcode,
show in-stock/out-of-stock Foodpanda items, or find items with a missing Foodpanda price.
The **Foodpanda Price** cells are editable: after making the required changes, click **Save
Foodpanda Prices** to update that branch's own Foodpanda Price List. The save checks that no
one else changed the same prices after the report loaded, so a newer correction is not silently
overwritten.

For a larger offline update, use the report's normal **Export → Excel** action, change only the
**Foodpanda Price (Editable)** cells, then choose **Foodpanda → Import Updated Excel** on the same
report and upload that `.xlsx` file. The importer finds the report header even when the export
includes filter rows, identifies each row by Item Code, and updates only the selected branch's
Foodpanda price. Blank or non-positive prices are skipped and reported; duplicate Item Codes are
rejected. All other exported columns—including selling price, MRP, stock, active, quantity, cost,
and barcodes—are ignored and can never be imported back as master or stock changes. Each run keeps
the source workbook and counts on a Foodpanda Price Import Log.

Click **Download Foodpanda CSV** to get the vendor-upload shape
(`barcode, sku, price, active, quantity`) for the rows currently shown by the report filters.
The report fills `active` and `quantity` automatically from currently sellable stock in the
branch's finished-goods warehouse (`actual stock - reserved stock`); staff do not type or upload
stock back into ERPNext. Rows without a primary barcode or a positive Foodpanda price are skipped
and counted in the on-screen message. MRP continues to come from the branch's own selling price
list, not the global Item MRP. For an item whose latest cost comes from migrated opening stock,
the report keeps the Stock Entry rate as **Cost Excl. Taxes** and reconstructs **Cost Incl. Taxes**
with the item's FBR tax rate—the opening migration deliberately kept GST out of stock valuation
and posted it separately, so Stock Entry `valuation_rate` itself is not tax-inclusive.

To push that same CSV to Foodpanda over SFTP (per-branch credentials, manual or scheduled),
see the Roman Urdu KPO/admin guide:
[Foodpanda SFTP — KPO & Administrator Guide (Roman Urdu)](foodpanda-sftp-kpo-guide-roman.md).

**Partner API synchronized outlet** (catalog import/map, Catalog Sheet, price/stock/active
push, orders, outlet status) is documented in English here:
[Foodpanda synchronized outlet — done, left, and how it works](foodpanda-synchronized-outlet.md).
Site-wide credentials and webhook setup: open **Foodpanda Settings** in Desk and use
**Show README**, or read
[`aimatic/doctype/foodpanda_settings/README.md`](../aimatic/aimatic/doctype/foodpanda_settings/README.md).
That is the path toward keeping each Foodpanda outlet in sync from ERPNext without
manual portal uploads.

### FBR e-invoicing (Pakistan tax compliance)

Every POS sale is automatically reported to Pakistan's Federal Board of Revenue (FBR) as
required by law, with the correct tax category and rate calculated per item. Refunds are
reported to FBR independently of the original sale, so a return is never mistaken for a
duplicate of the original transaction.

By default, a till (POS Profile) whose branch has no FBR e-invoicing set up yet simply cannot
ring up sales at all — this is deliberate, since normally every sale must be tax-reported. For
a branch that genuinely isn't FBR-registered yet (for example, a brand-new store still being
set up, or below the registration threshold), an implementer can check "Allow Sale Without FBR"
on that till's POS Profile. With that switch on, sales at that till complete normally with no
change to the customer's price, just without any GST line or FBR e-invoice — the receipt looks
like any other receipt, with no FBR section shown at all, rather than showing a confusing
"rejected" message. Leave the switch off for every till that should keep FBR mandatory, which is
every till in normal day-to-day operation once a branch's FBR settings are properly configured.

### Barcode and shelf-label printing

Each ERPNext Price List form includes a **Search Barcode** action. Staff can scan or enter a
complete barcode to open that Price List's matching Item Price row. The Item list and the Item
Price report reached through **Add / Edit Prices** also provide the same barcode search action.
When Item Price records are downloaded through **Export Data**, the exportable **Barcodes** field
contains every barcode configured on the linked Item, in barcode-row order and comma-separated.

Staff can print barcode labels or A4 shelf-price labels directly from a submitted Purchase
Receipt, Delivery Note, Sales Invoice, or stock transfer — with configurable label templates
(size, columns, fonts, which fields appear) so different label types (small barcode stickers
vs. full shelf-price cards) can be maintained without any code changes.

The print layouts themselves — barcode labels, shelf labels, the purchase receipt/invoice print
layouts, and the POS receipt layouts — ship automatically with the app. POS customer receipts show each item's plain barcode text directly below its description when a barcode is available. An implementer setting
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

### Retail finance setup and controls

Accounts staff and System Managers can open **Aimatic → Finance Setup** to run a read-only
readiness review for a company. It checks the Company/chart of accounts, store Branch → Cost
Center → Warehouse mappings, POS Profile accounting context, cash/bank and payment mappings,
receivable/payable accounts, inventory valuation foundation, tax setup, and forward Branch
coverage. The page never changes accounts or entries; its buttons open the relevant ERPNext
records for an authorized user to review.

The complete framework and missing-feature register are in
[`docs/retail-finance-setup.md`](retail-finance-setup.md). Existing supplier, inventory, and
accounting openings from the iPOS cutover are the accepted baseline. Unavailable history is not
reconstructed; reporting proceeds forward. Store balance sheets, deposit reconciliation, petty
cash controls, head-office allocation, physical shrinkage, supplier rebates, certified branch
EBITDA, and stock/POS/subledger-to-GL reconciliations remain explicit separate work rather than
being presented as finished.

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
- **POS Supervisor** — can process refunds, close a shift, void an item, and authorize a normal
  cashier to refund, void an item, clear a full cart, or close a shift on the POS terminal.
  Those step-ups ask for fresh online supervisor credentials (password is never remembered;
  the last supervisor username may be prefilled).
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
   Administrator/System Manager transactions use the company's only Branch automatically; if the
   company has multiple Branches, saving is blocked until the administrator explicitly selects one.
3. Setting up POS Profile(s) and terminal(s) for that branch.
   Android terminals additionally require a supervisor-generated one-time enrollment QR; the
   public OAuth client is installed automatically by the Aimatic migration.
4. If migrating from the old iPOS software, running the item and supplier import scripts in
   `ipos_data_migration/` for that site.
5. Saving a new Branch automatically creates and baseline-populates its enabled selling-only
   `<Branch> Selling Price List`. For a branch created before this behavior existed, use Finance
   Setup → **Initialize branch price lists**; the action is idempotent and never creates a buying
   Price List.
6. Open **Aimatic → Finance Setup**, run the readiness review, and resolve every critical block
   before forward operations begin. Warnings must have a named owner and follow-up date.

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
