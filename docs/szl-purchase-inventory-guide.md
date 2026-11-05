# SZL Purchase & Inventory Operating Guide

**Maintained operating document · 5 November 2026 · Asia/Karachi**

For branch managers, stock controllers, procurement officers and Accounts.

This guide explains the installed SZL workflow, how replenishment is calculated, what evidence to check, and where corrections belong. It separates current capabilities from proposed improvements. The system identifies recorded problems and prepares requirements; staff still verify physical stock, choose commercial terms and place orders.

## 1. Start here: what is live

Open [Retail Operations](https://szl.aimatic.tech/app/retail-operations-console). Select the branch first, then Inventory or Purchase. Use Sales for cost exceptions and Accounts for supplier balances and voucher events.

| Capability | Current behavior |
|---|---|
| Branch stock | Each branch uses its mapped finished-goods warehouse. Branch and company must agree. |
| Inventory | Negative balances, selling-stock risks, low cover, overstock and slow-moving items are visible. |
| Purchasing | Vendor proposals contain separate Ready and Review lines, previous receipt context, outstanding orders and recorded purchasing commitments. |
| Historical data | Rollout works with imperfect data. It does not silently repair stock, rewrite invoices or merge product codes. |
| Automatic Material Requests | Implemented but globally and branch-wise OFF at the documented rollout. Activation requires reviewed rules and sound current evidence. |
| Purchase Orders | No automatic supplier order placement or PO submission. Procurement decides and records the real order. |
| Discord | S1's Inventory, Accounts, Sales and Purchase webhooks are shared by six branches initially. Each branch can later use its own destinations. |
| Payment vouchers | Prospective Payment Entry creation, submission and cancellation events go to the resolved branch's Accounts destination. Events clearly distinguish draft, submitted and cancelled. |
| AI | Optional explanation of recorded evidence. Quantities, routing, permissions and document writes follow deterministic code. |

The six configured branches are S1 Ghouri Town VIP, S2 Bhatta Chowk, S4 Wallayat Complex, S5 Sector C, S6 Khalid Block and S7 Empire Heights. A store's familiar name does not replace the actual Branch-to-Warehouse mapping.

**Important distinction:** a suggested order is a requirement estimate. A Ready line means no implemented review flag was found; it is not a supplier confirmation, physical count, price guarantee or purchasing approval.

## 2. Daily responsibilities

### Branch manager / stock controller

1. Open Inventory for the correct branch and check the evidence timestamp.
2. Review Stockout risk and Low cover for products currently selling.
3. Check negative quantities against the shelf, store room, stock ledger, unclosed POS and possible alternate item codes.
4. Review overstock before requesting more stock or offering a transfer.
5. Search Tracked issues by item code or name. Acknowledge a finding, record the investigation and move it to Action underway when someone owns it.
6. Correct the actual source through the appropriate ERPNext document. Refresh evidence and verify the result. Merely marking an issue Resolved does not change stock; persistent evidence may reopen it.

### Procurement officer

1. Open Purchase for each branch, then inspect the vendor proposal and outstanding supply.
2. Record orders already placed by phone, WhatsApp or other channels before ordering again.
3. Keep Ready lines and Review lines separate. Resolve quantity/source/reference questions before committing flagged quantities.
4. Download the full vendor CSV. Discord is a short digest, not the complete order list.
5. Check current supplier prices, availability, pack terms and practical quantities. Historical receipt quantities are context, not a verified minimum order.
6. Place the order outside the bot, then use Record order placed with the actual quantities and supplier reference.
7. Add a genuine expected arrival date only if known. Match subsequent receipts to the commitment and investigate outstanding quantities.

### Accounts

Review the Accounts channel and supporting voucher links. Distinguish invoice outstanding balances, unbilled receipt estimates and payment events. A voucher being created, or even submitted in ERPNext, does not prove a bank settled it. Reconcile payment references and bank evidence separately.

## 3. Where the inventory evidence comes from

The branch's recorded position starts with its finished-goods warehouse's Bin balance. Demand uses submitted POS stock-unit quantities, grouped by item and warehouse. Non-return sales build selling pace; returns remain separate evidence rather than being treated as negative demand.

The current day is excluded from the selling history. The demand window is set per branch in Branch → Demand Window (Days): 28 completed days by default, allowed from 7 to 90, and never longer than Retail Operations Settings → History Days. Its denominator is shortened only when fewer days than the window have elapsed since the branch's first submitted POS document. It is a branch-age denominator, not a separate launch date for every product. The recent window is up to seven completed days. Calendar days without recorded sales count as zero; this is not an estimate of sales lost while the shelf was empty.

Submitted POS invoices that have not yet been consolidated/closed can represent sales not yet reflected in the stock ledger. The engine subtracts their pending stock effect once. Old unconsolidated sales are flagged for review. Investigate the real POS closing/stock impact rather than blindly closing documents to change the report.

Reservations, outgoing transfer requirements and outstanding replenishment documents also affect the decision. Therefore the numbers Recorded stock and Qualified available intentionally differ.

### Read these Inventory columns correctly

| Column | Meaning / action |
|---|---|
| Recorded | Warehouse ledger/Bin quantity; it is not a fresh physical count. |
| Qualified available | Recorded position adjusted for pending POS, positive reservations and outstanding outgoing transfer requirements. |
| Demand/day | The bounded observed-sales pace described below, in stock units per day. |
| Cover days | Qualified available divided by demand; floored at zero. A dash means there is no positive rate to divide by. |
| Status | A classification relative to selling pace and the calculated coverage policy. |
| Review reasons | Specific reasons a quantity/source needs checking before automation. |

A quantity of one can be comfortable for an item selling once a month and critical for one selling ten per day. Low stock is therefore a time-and-demand decision, not a universal quantity threshold.

## 4. Exact reorder calculation

This section documents the current implementation. Displayed values are rounded; the decision uses the underlying calculation. All quantities are stock units, not assumed carton counts.

### 4.1 Demand rate

Let `W` be the branch demand window (default 28, bounded to 7–90 days and to History Days). Let `N = max(1, min(W, days from the branch's first submitted POS date through yesterday))`, or `W` when no such document exists; `SW` is sold stock units in that completed period; `S7` is sold stock units in the recent period of up to seven days.

```text
long_rate   = max(SW / max(N, 1), 0)
recent_rate = max(S7 / max(min(N, 7), 1), 0)

If long_rate > 0:
  demand D = max(long_rate, min(recent_rate, 1.5 × long_rate))
Otherwise:
  demand D = recent_rate
```

This lets a recent increase raise the requirement, while capping a single exceptional week at 150% of the longer rate. It does not immediately reduce the rate for a quiet recent week. Promotions, closures, shelf gaps and unusual demand still require judgement.

### 4.2 Coverage horizon H: receipt cadence, not supplier lead time

There is no requirement for staff to supply lead times or minimum/maximum order quantities.

The starting fallback is seven coverage days for general goods. Categories whose names match bread, fresh, eggs, dairy or milk use a two-day fallback/cap to reduce excessive fresh-stock coverage. Category-name matching is a practical rule, not verified shelf-life management; review misleading category names.

Historical positive purchase receipts provide arrival events. Partial receipts attached to the same PO share the first receipt event, so one order split into several deliveries does not create several independent ordering cycles. The system calculates positive gaps between distinct receipt dates.

With fewer than two gaps, it uses the provisional category policy. Otherwise it takes the median gap, excludes unusually long gaps above the larger of twice that median or three days, then takes the rounded-up median of the remaining gaps. Coverage is capped; general cadence cannot exceed 28 days and the current default general category cap is 21 days. Fresh coverage stays within two days.

**This is observed replenishment spacing.** Same-day PO/PR/PI entry cannot establish order-to-delivery time. A late replenishment gap also cannot establish a supplier's normal lead time. The field for inferred supplier lead time remains unavailable.

### 4.3 Observed-sales replay can replace the starting horizon

Where there are at least four distinct receipt events, usable prior sales and at least two regular evaluation cycles, the engine compares candidate coverage horizons of 1, 3, 7, 14, 21 and 28 days, subject to category caps.

For each cycle it uses only sales before that cycle to estimate its initial demand rate, then walks forward through the actual recorded sales. Exceptionally long receipt gaps are excluded. It selects the candidate minimizing:

```text
5 × observed shortfall-days + average excess-cover-days
```

The shortfall and remaining-stock measures are normalized to the observed rate. The factor five is the current published policy preference for avoiding recorded shortages. It is not a financial cost model, a forecast of unobserved lost sales, or a guarantee that the shelf stayed full. With insufficient evidence, the cadence/fallback policy remains visible.

### 4.4 Safety stock, trigger and target

Let `σ` be the population standard deviation of daily recorded sales, including zero-sale calendar days in the demand window.

```text
safety_days = min(max(0.25 × H, 1), 3)
safety_qty  = max(D × safety_days,
                  min(σ × sqrt(H), D × H))

reorder_level R = D × max(1, H/2) + safety_qty
target_qty T    = D × H + safety_qty
```

For the fresh-category policy, `T` is capped at `D × category_days`, and `R` cannot exceed that target. These are quantity policies derived from selling pace and observed replenishment, not manually supplied supplier minimums.

### 4.5 Available position and supply

```text
physical_adjusted = recorded_qty
                    − pending_POS_qty
                    − max(reserved_qty, 0)

available A = physical_adjusted
              − max(outgoing_transfer_requirement, 0)

eligible_position = A + max(qualified_incoming, 0)
```

Qualified incoming requires outstanding supply supported by a genuine recorded commitment with an expected date that is today or later. Native PO schedule dates alone do not establish a confirmed arrival. An unknown or overdue arrival generates a review flag, rather than automatically becoming available stock.

An incoming order that arrives after estimated stockout is separately flagged for expediting. Although it appears in the incoming position, its lateness blocks automatic requests and demands a procurement decision.

Outstanding Material Requests are requirements, not delivered stock. They block duplicate automatic requests but are not counted as confirmed incoming inventory.

### 4.6 Proposed quantity and rounding

```text
shortage_trigger = (D > 0 or an outgoing transfer requirement exists)
                   and A <= R

If shortage_trigger:
  raw_proposal = max(T − eligible_position, 0)
Otherwise:
  raw_proposal = 0
```

Whole-unit stock rounds upward to the next whole unit. Other units round to three decimal places. The engine does not infer an MOQ or supplier pack multiple from historical orders. Procurement may amend the actual order for known commercial terms, then records what was really ordered.

A flagged item can still display a calculated quantity to help investigation. That quantity is not an instruction to buy the flagged item.

### 4.7 Worked example

Assume a branch on the default 28-day window and a general-goods item with 28-day sales of 112 units, seven-day sales of 35 units, selected horizon seven days and daily sales standard deviation two units.

| Step | Result |
|---|---|
| Long rate | 112 / 28 = 4 units/day |
| Recent rate | 35 / 7 = 5 units/day |
| Selected demand | max(4, min(5, 6)) = 5 units/day |
| Safety days | 7 × 0.25 = 1.75 |
| Safety quantity | max(8.75, min(2 × sqrt(7), 35)) = 8.75 |
| Trigger | 5 × 3.5 + 8.75 = 26.25 units |
| Target | 5 × 7 + 8.75 = 43.75 units |
| Position | Recorded 18 − pending POS 2 − reserved 1 = 15 available |
| Cover | 15 / 5 = 3 days |
| Qualified incoming | 10 units |
| Eligible position | 15 + 10 = 25 units |
| Proposal | ceil(43.75 − 25) = 19 units |
| Estimated cost at 120/unit | 19 × 120 = 2,280 in company currency |

This assumes the incoming ten units meet the arrival-evidence checks, the source is supported and there are no other flags. If an outstanding MR already exists, review/fulfil it instead of creating another. If available stock were above 26.25, the engine would not trigger a new proposal in this example.

## 5. What each inventory status means

The classification has an order of precedence. Negative recorded/adjusted stock comes before ordinary low-stock decisions.

| Status | Current rule | First action |
|---|---|---|
| Negative stock | Recorded or physical-adjusted quantity is below zero | Count stock and inspect ledger/reference/POS effects. |
| Slow moving / No demand | No positive demand or outgoing transfer need; positive stock is Slow moving | Review range, ageing and sales evidence; avoid speculative buying. |
| Stockout risk | Selling/transfer need exists and available is zero or below | Verify physical availability and expedite supported supply. |
| Low cover | Available is at or below the calculated trigger | Review the proposal and existing orders. |
| Overstock | Available exceeds twice calculated target | Reduce buying; check transfers, returns and demand quality. |
| Healthy | None of the preceding conditions applies | Monitor. This does not certify physical stock or valuation. |

An historical negative with no current recorded demand remains in Inventory, but is deliberately kept out of the vendor buying pack unless there is an outgoing transfer requirement. Buying to hide an old ledger error is not a valid correction.

## 6. How suppliers, prices and proposals are chosen

Positive, non-return receipts in the configured historical window establish supplier support. The default supplier is the one with the most distinct receipt documents for that branch/item; the most recent receipt date breaks ordering ties. If two suppliers have equally supported receipt counts, the item gets a source-review flag even if one is displayed first.

The last receipt from the selected supplier provides its last unit cost, last receipt, last received quantity and linked PO context. If no supplier is supported but historical transfers exist, a source warehouse can be suggested. A reviewed Retail Replenishment Rule can explicitly set exactly one supplier or source warehouse.

### Estimate versus final order cost

```text
last_stock_unit_cost = receipt base_net_rate / conversion_factor
line_estimate       = proposed_stock_units × last_stock_unit_cost
vendor_estimate     = sum of Ready-line estimates only
```

The estimate is in company currency and uses historical net receipt rates. It is not a current supplier quote. Current prices, taxes, discounts, delivery charges and actual ordered quantities can change the final invoice. Review lines are excluded from the vendor Ready-line total.

A receipt's conversion factor is used to interpret an already recorded transaction in stock units. This does not authorize guessing a carton/bottle relationship or inventing a new product conversion.

### Ready versus Review

Ready means no implemented flag was found. Review can include negative/oversold stock, alternate references, disabled items, an unsupported or ambiguous source, old pending POS, an outstanding MR, unknown/late incoming supply, batch/expiry constraints or insufficient donor capacity. Inspect the reasons, not only the label.

Procurement must still check whether an order was placed but not recorded. The system cannot deduplicate an order it has never been told about.

## 7. Placed orders, receipts and fully received old POs

Use **Purchase → Record order placed** immediately after a real phone/WhatsApp/vendor order. Record the supplier's reference and actual ordered stock-unit quantities. Expected arrival is optional; do not create a fictitious date to make a line look ready.

A Retail Purchase Commitment records this purchasing decision. When a native PO is linked, its unreceived quantity supplies the outstanding-order evidence; the commitment is not counted again as a second order. Direct receipt links or receipt allocations can instead match one or multiple actual receipts. Matching requires the same supplier, company, branch, warehouse and item. A receipt row cannot be consumed twice. Submitted returns reduce the effective received quantity; cancelled source documents do not count as valid receipts.

A completed native PO is not reopened when its stock later sells out. The engine treats new low-cover/stockout evidence as a new requirement, retaining historical receipt/PO context. The Purchase digest is vendor-wise replenishment reporting, not an exact allocation of every sold unit to one old PO. There is no supported claim that a particular PO's physical batch is depleted unless separate batch evidence establishes that.

If an order is delayed, investigate and update its commitment. If no real goods are outstanding, close/cancel the relevant commitment or native order through its valid workflow. Do not create a false receipt to silence an alert.

## 8. Transfers and donor protection

A reviewed source warehouse can fulfil a transfer requirement. The donor's available capacity is its recorded stock minus positive reservations, pending POS, other outstanding outgoing transfer requirements and three days of its own observed demand. A donor must be an enabled, non-group warehouse in the same company with a branch mapping.

Allocation goes to the lowest-cover recipients first within that evaluation. If the donor cannot cover the entire requirement, the remaining need is labelled for a source decision and automatic eligibility is removed. The system does not silently choose a replacement vendor for the uncovered remainder.

Automatic MR creation is serialized with a site-wide lock and recomputes current evidence. Staff must still avoid unrecorded competing manual transfers. The three-day donor reserve is the implemented policy, not a guarantee of the donor's actual delivery cycle.

## 9. Material Requests: how to enable carefully

Automatic requests are implemented but OFF at the recorded rollout. The approval gates are:

1. Retail Operations Settings → Enabled and Automatic Requests.
2. Branch → Enable Operations Reports and Enable Reviewed Automatic Material Requests.
3. A unique item/branch Retail Replenishment Rule that is enabled, reviewed and explicitly allows automatic requests.
4. Exactly one approved supplier or source warehouse, a positive requirement and no remaining implemented review flags.
5. No competing native Item Reorder rule for the same warehouse/item; no outstanding submitted MR for that requirement.
6. Current stock/reservation rechecks still match the calculation; transfer capacity remains protected.

Passing these gates creates and submits a native Purchase or Material Transfer Material Request, grouped by source. It does not submit a supplier PO. Its requested schedule date is tomorrow; this is an internal requested date, not a supplier delivery promise.

Start with a small, frequently reviewed set of clean items in one branch. Expand only after physical stock, received quantities and duplicate-order handling agree. Do not turn on all-item automation to satisfy rollout pressure while reference/valuation problems are unresolved.

## 10. How and where to fix common problems

These are investigation paths. Source-document corrections require appropriate ERPNext permissions and a clear audit trail. The reporting bot does not perform these corrections for you.

| Symptom | Check first | Where the correction belongs |
|---|---|---|
| Negative balance but stock is on shelf | Correct branch/warehouse, transaction dates, actual count, POS closing and alternate code | Original receipt/transfer/sale workflow, or an approved current Stock Reconciliation supported by a count. |
| Two codes appear to represent the same bottle | Brand, size, barcode, stock UOM and actual physical identity | Item/barcode setup and Retail Product Reference review; choose a verified primary selling identity for future activity. |
| Similar products are different brands | Brand and physical specification | Keep separate item identities and separate stock. Do not net their quantities. |
| Quantity looks wrong by a pack factor | Transaction UOM and its existing conversion factor | Item/UOM and the affected native transaction, after verification; no guessed carton conversion. |
| Old pending POS reduces availability | POS closing/consolidated invoice link and its real SLE effect | Native POS closing/consolidation investigation; do not double-subtract or fabricate movements. |
| Outstanding PO already arrived | Actual received document, supplier/item/warehouse match, cancelled/returned documents | Correct native PO/PR references and commitment receipt matching. |
| Proposed duplicate order | Open MRs, POs, commitments, unrecorded WhatsApp orders | Record the real order; fulfil or correctly close obsolete requirements. |
| Wrong supplier / no source | Receipt support, equally supported vendors, current supplier relationship | Review a Retail Replenishment Rule and supplier/item setup. |
| Low cover despite substantial incoming | Arrival unknown, overdue, or after estimated stockout | Confirm/update the real arrival and expedite; do not merely increase the order. |
| Overstock | Demand history, wrong unit, duplicate receipts, transfers, ageing/expiry | Correct source errors first; then reduce ordering or arrange a genuine transfer/return. |
| Zero or implausible cost | Sales SLE cost, stock history, receipts and unit conversion | Valuation/source transaction investigation by Accounts and stock control. |
| Wrong branch report | Branch company, finished-goods warehouse and warehouse branch tag | Branch/Warehouse settings; never relabel transactions solely to move an alert. |

### A practical negative-stock investigation

Open **Inventory → Review tracked issues → Negative stock**, search the item and open Stock ledger. The link carries its company, item and warehouse. Compare movements with Purchase Receipts, Stock Entries and the relevant consolidated sales. Check pending POS separately. Count the item physically and search verified alternative codes.

Record what was found in the issue status reason. If the recorded quantity is wrong, correct the supported source or approve a current reconciliation. After refreshing evidence, confirm both the stock position and the issue. Do not treat the same scan timestamp on several issues as proof that all their stock movements happened together.

### Product reference review does not clear a ledger

The system suggests exact normalized full-name/brand/unit matches when balances split across positive and negative codes. A reviewer can confirm or reject an identity relationship in Retail Product Reference. Confirmation records the relationship; it does not move stock, certify physical availability, sum balances into an approved quantity or erase the negative code. Linked references continue to block blind quantity automation until the source/physical problem is addressed.

## 11. Cost and margin checks supporting inventory decisions

The Sales report uses the installed authoritative POS gross-margin report's recorded stock-ledger cost. A cost being present in a ledger is not proof it is correct.

Zero/wrong-sign or missing costs, sales crossing negative stock, margins above 60%, and unit costs differing by more than 50% from the latest branch receipt within 90 days are held in Cost / valuation review. These thresholds flag investigation, not proven error. Receipt comparisons are bounded at 20,000 rows; an exceeded bound is disclosed rather than silently treated as verification.

Negative recorded margins and lowest non-negative margins are separate. Sale value, recorded cost, quantity and profit are visible. Returns have their own quantities, refund value, cost reversal and net recorded effect. A flagged net result remains provisional. Receipt prices are comparison evidence; they never replace COGS automatically.

For example, zero recorded cost can create an apparent 100% margin, and negative-stock valuation can create implausible oil margins. Neither should be promoted as a winning product. Investigate the source cost and units before changing selling prices or judging staff.

## 12. Discord reports and payment voucher alerts

Shared destinations retain branch names in every branch card. The Purchase digest groups priority sources/vendor proposals, showing Ready and Review separately. A digest is capped at eight messages per destination; it explicitly links to omitted detail. Download the full vendor CSV before placing an order.

Branch evidence is evaluated about hourly through a 15-minute scheduler check. Daily channels use the times in Retail Operations Settings. The delivery outbox is drained every minute. Changed urgent inventory findings have their own deduplicated notification path; historical backlog was baselined to avoid a rollout flood.

### Payment Entry events in Accounts

New Payment Entries of type Pay, Receive or Internal Transfer queue a **Created** alert. Submission and cancellation each queue their own event. Re-saving a draft does not create another creation alert. The event is a snapshot of its state at that time; later lifecycle events are separate messages, not a rewrite of the financial record.

Cards show branch, voucher link, type, status, posting date, event time, party, source/destination amounts with their respective currencies, method and a bounded list of linked documents. They omit bank account numbers and free-text remarks. A created draft is explicitly not posted. Submission is not asserted to be bank settlement.

Routing first uses the Payment Entry branch. With no branch, a unique exact company/cost-center-to-Branch match can be used. Conflicting company/cost-center, a missing or ambiguous branch, or no Accounts destination prevents misrouting and records a sanitized Error Log entry. Fix the mapping before expecting alerts; no company-wide branch guess is made.

Only Payment Entry is covered. Generic Journal Entries, opening entries, direct GL changes and arbitrary bank activity do not generate payment-voucher alerts. There is no historical backfill. Newly amended vouchers have their own identity and lifecycle events.

The alert outbox is written in the voucher's database transaction. If that transaction rolls back, its alert rolls back too. No Discord HTTP request runs inside payment creation/submission/cancellation. Notification errors are isolated so they do not block the financial save. A shared Accounts webhook receives one event for its branch, not six copies.

### Delivery recovery

Use **Retail Operations → Delivery health**. Pending/Retry wait for the sender; rate limits use bounded retry timing. Failed requires investigation. Uncertain means the remote outcome could be unknown: inspect Discord before allowing a resend. A destination change is checked against the represented branches, so an old report is not casually redirected to a different branch's channel.

If a mapping error prevented a payment alert from being queued, the financial voucher still exists. Investigate Error Log and correct the mapping; historical vouchers are not automatically replayed merely by re-saving them. Any deliberate replay should be reviewed and clearly labelled.

## 13. Known limitations and improvement priorities

| Priority | Improvement | What makes it safe to progress |
|---|---|---|
| First | Correct branch/warehouse mappings, pending POS exceptions and stock identity problems on actively selling lines | Physical counts, matched source documents and clear issue ownership. |
| First | Record orders at placement and match actual receipts | Procurement uses commitments consistently; no hidden WhatsApp orders. |
| Next | Review routes for the highest-turnover items | Agreed supplier/source, trustworthy stock UOM and no unresolved reference or supply flags. |
| Next | Pilot automatic MRs | One branch, selected reviewed rules, duplicate checks and daily review. |
| Next | Improve fresh/batch/expiry policies | Verified category/shelf-life and batch availability evidence; current rows remain review-gated. |
| Later | True supplier delivery performance | Real order-placement timestamps and actual first/completed receipts captured prospectively. |
| Later | Better seasonal/promotion and lost-sales treatment | Recorded campaign/availability evidence and validation across branches. |
| Later | Exact old-PO/batch depletion alerts | Verified batch or allocation evidence; current aggregate stock is insufficient. |

The effective demand window is the smaller of the branch's Demand Window and History Days (the installed default is 90), so lowering History Days also shortens every longer branch window. Newly introduced products in an older branch share its denominator and can initially be understated; a longer window extends that effect.

Choosing the window: a 28-day window reacts quickly but gives zero demand to items with no sale in 28 days and noisy rates for items selling a few days a month. A 90-day window steadies those items but reacts more slowly to falling demand and keeps a branch's opening-period sales in view for 90 days. The window does not lengthen by itself; it is a reviewed branch setting. A read-only S1 replay on 5 November 2026 (14 September to 29 October) gave a 90-day fill rate of 87.4% against 81.1% for 28 days, with about 18% more stock held. Most of the gain was in slow and dormant items. This was a simplified replay on one branch, not a forecast.

The current demand calculation can understate demand after prolonged stockouts because unsatisfied customers leave no sales quantity. It cannot see unrecorded theft, damage, stock on the wrong shelf or undocumented supplier orders. No algorithm removes the need for physical control.

Voids come from POS Void Log, which tills fill when a cashier removes a cart line, clears the cart or deletes a held sale. The Sales card lists the busiest counters by void type (Line removed, Cart cleared, Held sale deleted) with value (quantity × selling rate before tax) and how many had no supervisor approval because the cashier's own role allowed them; review those first. Tills on POS versions before the void log report nothing, and an offline till sends its voids when it reconnects, so do not read an empty day as proof of no voids. A reason is recorded only on POS Profiles with Require Void Reason checked (off by default for retail). Independent cash reconciliation remains a separate evidence requirement.

## 14. Controls, rollback and document maintenance

Use Retail Operations Settings for global evaluation, Discord and automatic-request controls. Use Branch for its warehouse mapping, operations flags, demand window and four private webhooks. Use reviewed rules for item sourcing and request activation. Use purchasing commitments for real placed orders. Use Tracked issues for investigation ownership and audit reasons.

To stop notifications, turn off Discord Enabled; to stop evaluation, turn off Enabled or the relevant jobs. Retain snapshots, commitments and delivery history. Do not delete audit evidence. Review downstream links before cancelling any generated native request. Database restoration is an outage/recovery decision that would discard newer production changes; disabling the feature is the first containment step.

**Canonical source:** `docs/szl-purchase-inventory-guide.md` in the Aimatic repository. The companion PDF is generated from this source. Future changes to calculations, source/receipt matching, review rules, automation gates, branch routing, payment events or alert behavior must update this guide and regenerate the PDF in the same task/commit. Runtime changes should also update the bench's current-state record when it would otherwise be wrong.

**Verification baseline:** source-reviewed demand/coverage/supply formulas; test-site stock/request/permission/margin tests; native payment lifecycle and alert tests; production mapping/scheduler checks; labelled Accounts-channel verification. Test and rollout outcomes are recorded in the commit and operational record; a delivery test is not a financial transaction.

### Source map for maintainers

| Area | Owned implementation |
|---|---|
| Demand, cadence, replay, trigger, target, vendor grouping | `aimatic/retail_operations/calculations.py` |
| Branch stock, sales, receipts and supply evidence | `aimatic/retail_operations/data.py` |
| Commitments, receipt matches and donor capacity | `commitments.py`, `transfers.py`, `documents.py` |
| Snapshots, issue lifecycle and native MRs | `service.py` |
| Margin screening | `margins.py` |
| Discord routing, durable delivery, card layouts | `discord.py`, `presentation.py`, `tasks.py` |
| Payment lifecycle hooks and payloads | `payments.py`, app `hooks.py` |
| Desk reports and permission-aware APIs | Native Retail Operations Console Page; `api.py`, `permissions.py` |

**Change record:** 5 November 2026 — initial detailed operating guide covering the installed replenishment workflow, investigation/correction paths, margin screening and prospective payment-voucher notifications. Keep future changes here brief; Git retains the full history.

5 November 2026 — demand window is a per-branch setting (default 28, 7–90 days, limited by History Days) instead of a fixed 28 days.
