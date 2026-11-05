# Retail Operations

Operator guide: [SZL Purchase & Inventory Operating Guide](szl-purchase-inventory-guide.md) · [PDF](szl-purchase-inventory-guide.pdf). Keep both updated with operational behavior changes; rebuild using the bench Python and `scripts/build_retail_operations_guide.py`.

Entry: `/app/retail-operations-console` (also the Retail Operations workspace).
Site: SZL retail; first verify site role before any installation/runtime action.

## Configure

1. System Manager opens Branch → Discord Channels. Enter Inventory, Accounts,
   Sales and Purchase webhooks as Password fields. The same destinations may
   be reused across branches. Save the first branch, then use Discord → Copy Discord channels to populate selected branches server-side (existing destinations are preserved by default). Shared Purchase reports group by vendor, with
   branch destinations preserved. Secrets are never exported in payloads.
2. Enable Operations Reports on participating branches. Their finished-goods
   warehouse must belong to the branch and company and be active/non-group.
3. In Retail Operations Settings, Enable controls evaluation; Discord Enabled
   separately controls sending. All defaults are off. Scheduled digests use
   the configured local times, evaluated every 15 minutes. Branch calculations
   run hourly in the long queue. Refresh evidence also works before enabling
   schedules. Latest reports are available without Discord or AI credentials.
4. First record supplier orders already placed outside ERPNext as Retail
   Purchase Commitments. Otherwise unrecorded orders cannot prevent duplicate
   purchasing proposals. The console supports vendor CSV download and an
   editable Record Order Placed dialog. It does not contact the supplier.
5. Keep Automatic Requests off initially. Review an item/branch rule with an
   approved supplier OR source warehouse, enable it, and separately enable
   global and branch automatic requests after validating the proposal.

The feature needs no owner-supplied lead times, stock minimums or maximums.
Historical same-day PO/PR/PI dates are not supplier lead times. Receipt cadence
and observed-sales walk-forward comparisons select coverage policies. The
published replay objective is 5× observed shortage days plus average excess
cover days. Unobserved lost sales cannot be reconstructed. Sparse histories
use labelled category defaults; fresh categories have bounded coverage.
Quantities are existing stock units. No inferred carton conversions or
contractual supplier minimums. Confirm ordering units before placing an order.

## Reports and controls

- Inventory: time-based low cover/stockout risk, negative balances, excess and
  slow stock, candidate duplicate references, linked-reference diagnostics and
  largest recorded movements. Largest movements are not automatically proof
  of physical irregularity. Reference grouping never changes stock or GL.
- Purchase: vendor order-ready/review sections, current open native POs,
  external purchasing commitments, last receipt/PO references, historical
  price estimates and source/coverage evidence. Unknown/overdue supply and
  uncertain stock prevent automatic request quantities for that line.
- Sales: submitted POS receipts/returns, large refunds, activity-adjusted
  counter comparisons and volume-labelled margin rankings using the existing
  tax-corrected SLE-cost report. Pending costing remains provisional. Same
  POS/consolidated sales count once. Invoice cancellations are a separate
  category from line and bill voids.
- Accounts: branch-tagged outstanding/overdue invoices by supplier/currency
  and separate receipt-based unbilled estimates. Cash reconciliation is marked
  unavailable until independently supported, not silently reported as zero.

Roles: Inventory Stock/Purchase/System Manager; Purchase Purchase/System
Manager; Sales Sales/System Manager; Accounts Accounts/System Manager.
Branch and Company User Permissions remain enforced. Internal full snapshots
and delivery records are System Manager only; report APIs return one permitted
channel and branch, not the whole snapshot. Branch secret fields are System
Manager permission level 1. The installer preserves existing standard Branch
permissions before adding custom grants.

## Commitments and partial receipts

Record the actual placed quantities, external reference, date and optional ETA.
Native PO item matching delegates supply coverage to that PO. Direct receipts
can be matched through Matched Partial Receipts, using the exact receipt item
row IDs; the Match Order endpoint supports appending matches. Rows must belong
to the same submitted vendor/company/branch/warehouse/item. A receipt row cannot
be allocated twice in the allocation table. A PO item cannot be assigned to
multiple commitment lines. Mixed PO/direct receipt matching for an item is
rejected. Receipt returns/cancellations affect calculated received quantities.
Scheduled evaluation refreshes the derived fulfilment state without changing
source financial/stock documents. Manual Completed/Cancelled states close a
commitment; a closed commitment cannot be silently reopened.

## Exceptions and delivery recovery

Issues support acknowledgement, action underway, resolution and expiring mute
with a required reason. Initial historical backlog is in digests rather than
an urgent flood. Changed urgent findings queue messages, with six-hour repeat
suppression. Stable findings are updated, not recreated. Daily digest revisions
edit the existing Discord message where content changes. Mention parsing is
disabled. A snapshot older than two hours is explicitly stale.

Discord digests use native rich cards: a branch overview followed by complete
branch cards, or priority vendor cards for Purchase. Ready lines and review holds
are shown separately. Stockout risks precede lower-priority cover exceptions;
vendors with ready lines precede review-only sources. Full-list omissions are
labelled with links to the matching branch and report in the console. Card fields
keep complete item blocks within Discord limits; descriptions and message counts
are bounded. Shared Inventory and Accounts packs include a short interpretation
card. Unchanged evidence timestamps alone do not trigger edits.

Delivery queue states: Pending, Sending, Sent, Retry, Failed, Uncertain, Skipped.
429 responses use bounded Retry-After. Network/5xx ambiguity is not blindly
retried. An interrupted Sending row becomes Uncertain. System Manager can use
Reconcile Delivery to record an existing message ID or confirm it was not
received before retrying. Changed destinations are checked against every
branch represented in the payload; old multi-branch content is not redirected
into a newly split branch channel. Provider response bodies and URLs are not
stored in errors. Only Discord HTTPS webhook hosts/paths are accepted and
redirects are disabled. Webhook-only deployment cannot read Discord replies.

AI explanations use the existing governed Nemotron/OpenRouter client only when
enabled. Inputs contain one authorised branch/channel report, no webhooks or
provider secrets. No tools or writes are exposed to the model. Provider errors
are sanitized and deterministic reporting continues. Conversational Discord
commands require a separate Discord application and are outside webhook setup.

The console's tracked issue queue defaults to active findings, with counts by
issue type, status filters, product/document search and 25-row pages. Inventory
rows identify the product, item code, warehouse, recorded quantity and stock unit,
sales pace and cover. Stock Ledger opens with the selected item's warehouse and
company; Details opens the existing issue and Update Status retains a required
reason. Last change describes the finding, not the stock movement time. The queue
uses branch/channel permission checks and returns selected evidence fields only.

Margin reporting screens the existing POS gross-margin report's recorded costs;
it never substitutes a receipt price for accounting COGS. Zero/wrong-sign costs,
sales crossing negative stock, item margins above 60%, missing costs and unit
cost differences above 50% versus the latest branch receipt in the preceding
90 days are held in Cost / valuation review. Thresholds are review rules, not
proof of bad data. Stock evidence is restricted to the branch warehouse;
receipt evidence to the company, branch warehouse, item and report date, with
a 20,000-row limit and an explicit unavailable-comparison note if exceeded.
Negative and lowest non-negative lists are separate. Ranked sale values/costs
exclude returns; item return units, refund values, cost reversals and their net
recorded result are separately disclosed. Flagged net results remain provisional.
Cost review retains an existing daily item-margin issue's identity. Financial
records and the underlying authoritative gross-margin report remain unchanged.

## Known data/integration boundaries

Historical cart voids cannot be reconstructed from supervisor authorization
attempts. Counter void totals read `POS Void Log`, which tills fill through
`aimatic.offline_pos.api.log_pos_voids` (removed lines, cleared carts, deleted
held sales; idempotent by till `eventId`). Tills older than the void-log
release send nothing; reporting explicitly labels this coverage.
Cash reconciliation is not claimed without independent source evidence.
Reference suggestions use exact normalized full name/brand/unit and split
balances as evidence, not automatic identity approval. Confirmed groups retain
availability uncertainty until reconciled; they do not silently net negatives.
No automatic historical corrections, PO submissions or supplier messages.

## Validation and scoped install

Run from the bench:

    bench --site siezal execute aimatic.retail_operations.setup.install
    bench --site siezal execute aimatic.retail_operations.test_integration.run

The explicit test runner refuses production. It uses savepoint rollback for
native MR/permission/commitment tests and fake HTTP responses for delivery;
no real Discord posts. Pure calculations can run through Python unittest.

The scoped installer reloads only owned Retail Operations doctypes/page/workspace,
adds branch secret/enable fields and two Material Request references, preserves
branch permissions, and registers two inert-until-enabled scheduler jobs. It
adds MR.branch only where the site lacks that field. It is repeatable and is
also registered as a patch and fresh-install step. No full fixture sync or
shared worker restart is required for the scoped metadata install. Verify the
HTTP-served page/API and scheduler registration after deploying owned code.

## Recovery

Disable global Enabled, Discord Enabled and Automatic Requests first; stop the
new Scheduled Job Type if needed. Existing unrelated POS operations remain
independent. Preserve generated audit records. Cancel unused Material Requests
through native ERPNext only after checking downstream links. Do not delete
orders already placed or change stock/GL to undo a report. Restore previous
owned code/configuration if necessary; retain additive tables/fields rather
than destructively dropping evidence. SZL requires a verified pre-change backup.
Full database restoration is a last resort and would also roll back later trade.

Digests are bounded to eight messages per channel destination by default, with explicit links for remaining detail. Delivery retries drain every minute; report evaluation/digest routing is checked every 15 minutes. Existing issues are seeded as a historical baseline while sending is off, preventing a delayed flood when webhooks are enabled.

## Payment voucher notifications

Payment Entry after_insert/on_submit/on_cancel append durable Accounts events
for creation, submission and cancellation. Draft saves do not repeat creation.
The event key includes doctype, voucher and lifecycle step; its outbox write shares
the financial transaction. The existing minute drain sends only after commit.
There is no HTTP request or Redis dependency in these hooks. Disabled operations
or Discord suppress alerts. Missing/ambiguous branch, conflicting company/cost
center or missing destination is logged without blocking the financial save.
Branch comes from the voucher or a unique exact company/cost-center mapping.
No historical replay; generic Journal Entries are outside this event source.
Cards disclose state and currencies, omit bank account numbers and free remarks,
and do not claim bank settlement. See the maintained guide for operating steps.
