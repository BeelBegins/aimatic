# Aimatic — Application Guide

Aimatic is the custom retail app that turns Frappe/ERPNext into a multi-branch supermarket
system: point-of-sale, purchasing, pricing, Pakistan FBR e-invoicing, barcode/shelf labels,
loyalty and gift vouchers, supplier management, and vendor performance reporting, all built
on top of one shared Branch → Warehouse → Cost Center structure.

This guide explains **what the app does and why**, in plain language, so that business
owners, implementers setting up a new site or branch, and developers new to the codebase can
all get oriented without reading source code. For the technical "how it's wired" detail
(hooks, doctypes, gotchas learned the hard way), see `CLAUDE.md` at the root of this bench —
this guide and that file are meant to be read together, not as duplicates of each other.

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

Stores use an offline-capable POS terminal application (a separate desktop app, not part of
this Frappe site) that keeps working even if the internet connection drops. Two distinct
logins exist on each terminal:

- **Cashiers** log in with their own username/password to open a till, ring up sales and
  process refunds (if their role allows it), and close out their shift at the end of the day.
- **Administrators/Supervisors** use a separate, more secure step-up login (valid for only
  5 minutes) to perform sensitive actions like resetting a cashier's PIN — this login requires
  a secure (HTTPS) connection and is fully logged for audit purposes.

Every sale and refund is recorded against the correct branch and warehouse automatically.
Stock and accounting only update once a cashier's shift is formally closed out at day's end —
not the instant a sale happens — this is expected behavior, not a delay or a bug.

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

## Setting up a new branch or site

At a high level, bringing a new branch or site online involves:

1. Creating the Branch record (its own warehouse and cost center are created alongside it).
2. Assigning staff to that branch (via a default Branch permission) so their sales/purchases
   post to the right books automatically.
3. Setting up POS Profile(s) and terminal(s) for that branch.
4. If migrating from the old iPOS software, running the item and supplier import scripts in
   `ipos_data_migration/` for that site.
5. The first time a Purchase Receipt for that branch applies a branch price update, its own
   Selling Price List is created and populated automatically — no separate setup step needed.

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
