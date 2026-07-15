# Ai Matic Restaurant

Ai Matic Restaurant is an isolated Android waiter product backed by ERPNext and the `aimatic`
Frappe app. It shares authentication and ERPNext business data with the other Ai Matic products,
but it has its own build profile, navigation, operational records, roles and API surface.

## Product boundary

The Restaurant app contains Tables, Orders, Menu, Activity and Profile only. It does not expose
Retail POS, Sales, Shopping, purchasing, stock administration, accounts or ERPNext reports.
Electron remains POS-only. Restaurant Android code must not import Electron IPC, desktop printing,
the desktop filesystem or other desktop-only services.

ERPNext remains authoritative for:

- users, roles and permissions;
- Company, Branch and POS Profile context;
- customers, warehouses and selling Price Lists;
- item eligibility, prices and stock availability;
- taxes and final POS Invoice validation;
- all accounting and stock ledger effects.

Restaurant Orders and Kitchen Tickets are operational records. They do not create accounting or
stock ledger entries by themselves.

## Installation and migration

The Restaurant schema is part of the main `aimatic` app; do not install a separate Frappe app.
After pulling the server commit, migrate each site that will use or administer Restaurant:

```bash
bench --site <site-name> migrate
```

Migration creates the Restaurant DocTypes, roles and the public `Aimatic Restaurant Android`
OAuth client. The OAuth callback is:

```text
com.beelbegins.aimaticrestaurant://oauth/callback
```

The Android app uses Authorization Code with PKCE, rotating refresh tokens and secure Android
storage. It never contains an API key, API secret or confidential OAuth client secret.

## Initial ERPNext configuration

Configure Restaurant in this order:

1. Confirm the Company, Branch, non-group Warehouse, selling Price List, walk-in Customer and POS
   Profile already work in ERPNext.
2. Create an enabled **Restaurant Profile**. Select its Branch, Company and POS Profile. The
   default Customer, Warehouse and menu Price List normally fall back to the POS Profile; use the
   optional overrides only when the restaurant genuinely differs.
3. Create one or more **Restaurant Floors** for that Branch.
4. Create **Restaurant Tables**, assigning each table to its Branch and Floor and setting capacity
   and display order.
5. Create **Restaurant Modifier Groups** and their options where required. Prices entered on
   modifier options are server-owned adjustments; the Android client cannot override them.
6. Create **Restaurant Menu Items** by linking existing enabled ERPNext sales Items. Supply the
   customer-facing menu name, Item Group category, kitchen station, preparation time and optional
   modifier groups.
7. Assign staff roles and ensure waiter users are permitted by the mapped POS Profile when its
   Applicable Users table is restricted.
8. Install the Restaurant APK, enter the ERPNext URL, and sign in with an individual user account.

Do not invent floors, tables or menus automatically during migration. These are business records
and must reflect the actual restaurant layout and service menu.

## Roles

- **Restaurant Waiter** — Android/API access to permitted profiles and assigned orders. This role
  has no Desk access.
- **Kitchen User** — restricted API access for Kitchen Ticket status changes at the permitted
  branch. This role has no Desk access.
- **Restaurant Manager** — Desk configuration and supervisory access across Restaurant records.
- **System Manager** — full configuration and diagnostic access.

The authenticated session user becomes the waiter identity. APIs never accept a client-supplied
waiter username as authoritative.

## DocTypes

| DocType | Purpose |
| --- | --- |
| Restaurant Profile | Maps Restaurant to Branch, Company, POS Profile, Customer, Warehouse and Price List context. |
| Restaurant Floor | Named floor/area within a Branch. |
| Restaurant Table | Physical table, capacity and floor assignment. |
| Restaurant Menu Item | Customer-facing configuration linked one-to-one to an ERPNext Item. |
| Restaurant Modifier Group | Required/optional single or multiple selections. |
| Restaurant Modifier Option | Child option with server-owned price adjustment and optional linked Item. |
| Restaurant Menu Modifier Group | Child mapping between a menu item and modifier group. |
| Restaurant Order | Active table, waiter, guests, ERPNext context, totals and optional submitted POS Invoice. |
| Restaurant Order Item | Item, quantity, sent quantity, authoritative rate, notes and modifier snapshot. |
| Restaurant Kitchen Ticket | Idempotent KOT created from quantities not previously sent. |
| Restaurant Kitchen Ticket Item | Immutable snapshot of each quantity sent to a kitchen station. |

Only one active Restaurant Order may exist for a table. Table status is derived from that order
and its item/KOT state; the server does not trust a client-provided table status.

## API surface

All Restaurant methods are under `aimatic.restaurant.api`:

- `get_public_config` — public OAuth client metadata only.
- `get_restaurant_bootstrap` — permitted profiles, context, floors, tables, menu, prices and stock.
- `get_table_order` — active permitted order for one table.
- `open_order` — atomically returns the existing active order or opens a new one.
- `save_order` — validates menu membership, modifiers, stock and ERPNext Item Price before adding
  unsent quantities.
- `update_unsent_item` — types/changes/removes an unsent quantity; sent rows are immutable.
- `send_to_kitchen` — creates one KOT for newly added quantities using a unique request ID.
- `update_kitchen_status` — Kitchen User/Manager transition through Queued, Preparing, Ready and
  Served.
- `request_bill` — requires a non-empty order with every quantity sent.
- `close_table` — requires a submitted matching POS Invoice containing the ordered quantities.
- `get_orders` and `get_activity` — permitted waiter operational views.

Rates, modifier prices, stock, waiter identity, Branch context and kitchen authorization are all
revalidated on the server. Reusing a KOT request ID returns the existing result and prevents a
duplicate kitchen submission.

## Status model

```text
Restaurant Order: Open → Sent to Kitchen → Bill Requested → Closed
Kitchen Ticket:   Queued → Preparing → Ready → Served
```

Cancellation is server-controlled. Sent rows and KOT contents are immutable so kitchen history
cannot be silently rewritten.

## Android modes and offline behavior

Live mode authenticates to ERPNext and uses only the restricted Restaurant API. Explore Demo uses
isolated mock data and never submits it to ERPNext. In live mode the app does not claim a kitchen
or bill action succeeded while offline. The action remains visibly unsent and can be retried after
connectivity returns.

## Verification and troubleshooting

- `get_public_config` failing means the site migration/OAuth patch has not completed.
- “No permitted Restaurant Profiles” means no enabled profile exists, the user lacks a Restaurant
  role, the user's default Branch differs, or POS Profile Applicable Users excludes the waiter.
- An empty menu means items are not enabled as Restaurant Menu Items, have no selling Item Price,
  are disabled/non-sales Items, or are unavailable under stock settings.
- A table conflict means another active Restaurant Order already owns that table; refresh instead
  of creating a second order.
- A duplicate KOT response is successful idempotency, not a second kitchen print.

Server-side changes are covered by CI tests. A release check should additionally verify Restaurant
OAuth deep linking and secure storage on a physical Android device.

## Deferred integration

The following remain intentionally deferred and must be implemented with atomic server workflows
and audit history:

- table transfer, merge and split;
- split billing and moving items between guests;
- POS Invoice/payment creation from the waiter device;
- durable offline mutation replay;
- scanner integration;
- push notifications and a dedicated Kitchen Display System.

