# Storefront Catalog API (v1)

Read-only feed of item catalog, branch-wise selling prices, and warehouse-wise stock for an
external online shopping website. This system (the aimatic/ERPNext backend behind the physical
stores) is the source of truth; your website's own database and checkout are entirely separate —
this API only lets you *read* catalog data on a schedule, it never writes anything back.

There are no write/order/checkout endpoints. Your site owns its own cart, order, and payment flow.

## 1. Authentication

You will be given an **API Key** and an **API Secret** tied to a dedicated integration account.
Send them on every request as an HTTP header:

```
Authorization: token <api_key>:<api_secret>
```

Example:

```bash
curl -H "Authorization: token 2c1bdd0e35c1121:4e4ce4823aa7ddd" \
  "https://<site-domain>/api/method/aimatic.storefront_api.api.get_sync_status"
```

- All requests must be HTTPS.
- Missing/invalid credentials return HTTP 403 with an empty body.
- Calling any endpoint without the credentialed account holding the correct access returns HTTP
  403 with `{"exc_type": "PermissionError", ...}`.
- These credentials are for **this one integration only**. Don't share them with any other system,
  and don't hard-code them in client-side/browser code — this is a server-to-server credential.

## 2. Response shape

Every endpoint is called as `GET /api/method/aimatic.storefront_api.api.<endpoint_name>` with
query-string parameters. Frappe wraps every successful response in a top-level `"message"` key:

```json
{"message": { ...the actual data described below... }}
```

Errors use Frappe's standard error envelope and a non-200 HTTP status code:

```json
{"exc_type": "DoesNotExistError", "_server_messages": "[\"{\\\"message\\\":\\\"Branch X not found\\\"...}\"]"}
```

In practice: check the HTTP status code first (200 = success), then read `.message` for the
payload.

## 3. Pagination

`get_items`, `get_price_list`, and `get_stock_levels` are paginated with the same shape:

```json
{"rows": [...], "next_start": 500, "has_more": true}
```

- Pass `limit_start` / `limit_page_length` to page through results (`limit_page_length` capped at
  1000 server-side regardless of what you request).
- `has_more: false` and `next_start: null` means you've reached the last page.
- Rows are ordered by `modified` ascending (then a stable tiebreaker), so paging through in order
  is safe even if rows change mid-sync — as long as you use `modified_after` for repeat syncs
  rather than re-walking pages 0..N from scratch every time (see §5).

## 4. Endpoints

### `get_sync_status()`

Call this first, before any heavier fetch. Cheap — just a `MAX(modified)` per resource.

```json
{
  "server_time": "2026-07-19 01:21:07.976494",
  "max_modified": {
    "Item": "2026-07-13 15:42:18.718617",
    "Item Price": "2026-07-14 21:32:33.316432",
    "Bin": "2026-07-16 21:02:36.126976"
  }
}
```

If none of these timestamps moved since your last successful sync, there is nothing new to fetch.

Rate limit: 120 requests / 60 seconds.

### `get_branches()`

No parameters. Small, unpaginated. Returns every branch (store location) and its warehouses.

```json
[
  {
    "name": "Ghouri Town Phase V",
    "branch": "Ghouri Town Phase V",
    "company": "Siezal Super Market",
    "cost_center": "Siezal Ghouri Town Phase V - ST",
    "default_selling_price_list": null,
    "warehouses": [
      {"name": "Ghouri Town Phase V - ST", "warehouse_name": "Ghouri Town Phase V",
       "disabled": 0, "is_default": true, "is_rejected": false},
      {"name": "Rejected Ghouri Town Phase V - ST", "warehouse_name": "Rejected Ghouri Town Phase V",
       "disabled": 0, "is_default": false, "is_rejected": true}
    ]
  }
]
```

- `default_selling_price_list` may be `null` — a branch's own price list is created lazily behind
  the scenes the first time a store's shelf pricing is updated for that branch. If it's `null`,
  `get_price_list(branch=...)` still works (see below) — it falls back to the global default list.
- `is_default` is the branch's normal sellable warehouse. `is_rejected` is a quarantine warehouse
  for rejected/returned goods — **do not show its stock as available for sale.**

Rate limit: 30 / 60s.

### `get_item_groups()`

No parameters. Flat list of every category/sub-category (ERPNext calls these "Item Groups"),
including group (folder) and leaf nodes. Build your own tree from `parent_item_group`.

```json
[
  {"name": "All Item Groups", "item_group_name": "All Item Groups", "parent_item_group": "",
   "is_group": 1, "lft": 1, "rgt": 654},
  {"name": "Genral", "item_group_name": "Genral", "parent_item_group": "Products",
   "is_group": 0, "lft": 3, "rgt": 4}
]
```

`is_group: 1` = a folder (has children, not itself an assignable category for an item).
`is_group: 0` = a leaf category an item can actually belong to.

Rate limit: 30 / 60s.

### `get_items(modified_after=None, limit_start=0, limit_page_length=500)`

Item master data. Paginated (§3).

```json
{
  "rows": [
    {
      "item_code": "8966000048805",
      "item_name": "Prema Desi Ghee 800g",
      "description": null,
      "item_group": "Desi Ghee",
      "brand": null,
      "stock_uom": "Nos",
      "image": null,
      "disabled": 0,
      "custom_mrp": 350.0,
      "modified": "2026-06-11 16:57:33.653162",
      "barcodes": [
        {"barcode": "8966000048805", "barcode_type": "", "uom": "Nos"}
      ]
    }
  ],
  "next_start": 500,
  "has_more": true
}
```

- **`disabled: 1` items are still returned, not silently dropped.** If an item gets discontinued,
  it will show up in your next `modified_after` sync with `disabled: 1` — hide/remove it from your
  storefront when you see that, rather than assuming absence means "unchanged."
- `custom_mrp` is the shelf/MRP reference price stamped on the item master itself (separate from
  the branch-specific selling price in `get_price_list` below — usually the same number, kept as a
  cross-check).
- An item can have more than one barcode (different pack sizes/units); `barcodes` is the full list.
- **A hard-deleted item will *not* reappear here even with `modified_after`** — see
  `get_deleted_items` below for that case.

Rate limit: 60 / 60s.

### `get_deleted_items(since)`

`since` (required): an ISO datetime string. Returns items that were **hard-deleted** (not just
disabled) after that timestamp — a genuinely separate case from `disabled: 1` above, since Frappe
never re-surfaces a deleted document through `get_items`' normal `modified_after` sync.

```json
[
  {"item_code": "8961103500458", "deleted_at": "2026-06-10 21:00:48.747412"},
  {"item_code": "8961014015683", "deleted_at": "2026-06-10 21:00:48.939477"}
]
```

In practice, this store's normal workflow is to **disable** an item rather than delete it, so this
list should usually be short/empty — but check it on every sync anyway, keyed off the same
`since` timestamp you used for `get_items`' `modified_after`.

Rate limit: 30 / 60s.

### `get_price_list(branch, modified_after=None, limit_start=0, limit_page_length=500)`

`branch` (required) — one of the `name` values from `get_branches()`. Paginated (§3).

```json
{
  "rows": [
    {
      "item_code": "8961008239194",
      "price_list_rate": 140.0,
      "currency": "PKR",
      "custom_mrp": 140.0,
      "valid_from": "2026-06-18",
      "valid_upto": null,
      "modified": "2026-06-18 18:22:10.701269"
    }
  ],
  "next_start": 500,
  "has_more": true,
  "price_list": "Selling - Ghouri Town Phase V"
}
```

- **`price_list_rate` is the final, customer-facing price** — this app's own shelf-pricing
  workflow keeps branch price lists in sync with the physical in-store shelf/MRP price. There is
  no separate tax-inclusive/exclusive flag to worry about (ERPNext's Price List doctype doesn't
  carry one) — treat the number as-is, what a walk-in customer actually pays.
  Rows already expired (`valid_upto` in the past) or not yet started (`valid_from` in the future)
  are excluded server-side.
- **Known data-quality edge case**: occasionally you may see the *same* `item_code` appear twice
  in one page, both currently valid. This reflects a real duplicate in the source pricing data, not
  a bug in this API. If you see this, take the row with the later `valid_from` (or `modified`) as
  authoritative.
- If a branch has no dedicated price list yet (see `get_branches` note above), this transparently
  falls back to the store's global default selling price list — you don't need to handle that case
  specially, just call it with the branch name either way.

Rate limit: 60 / 60s.

### `get_stock_levels(branch=None, warehouse=None, modified_after=None, limit_start=0, limit_page_length=500)`

Pass **either** `branch` or `warehouse` (not both required — a branch expands to its own
warehouse(s) automatically). Paginated (§3).

```json
{
  "rows": [
    {
      "item_code": "8961008239194",
      "warehouse": "Ghouri Town Phase V - ST",
      "actual_qty": 50.0,
      "reserved_qty": 0.0,
      "available_qty": 50.0,
      "modified": "2026-07-11 21:00:20.205987"
    }
  ],
  "next_start": null,
  "has_more": false
}
```

- `available_qty = max(actual_qty - reserved_qty, 0)` — this is the same formula ERPNext itself
  uses everywhere else in this system.
- **Important caveat, be conservative around zero**: `reserved_qty` only reflects a narrow set of
  reservation types (e.g. submitted Sales Orders) — it does **not** account for a sale that's
  in-progress at a cash register right this second, or the short window between a shift closing and
  stock actually updating (this backend batches stock/GL updates at shift-close, not per-sale).
  Treat `available_qty` as **indicative, not exact down to the last unit** — don't hard-block your
  own checkout at exactly `0`; consider a small safety buffer or a "low stock" threshold instead of
  a hard cutoff.
- If a branch has no warehouses mapped yet, you'll get an empty `rows: []` rather than an error.

Rate limit: 60 / 60s.

## 5. Recommended sync recipe

1. On first run: page through `get_items`, `get_price_list` (per branch you care about), and
   `get_stock_levels` (per branch) from `limit_start=0` until `has_more: false`. Store the
   `modified` value of the last row you saw, per resource.
2. On every subsequent sync (e.g. every few minutes):
   - Call `get_sync_status()` first. If none of the three timestamps moved past what you last saw,
     stop — nothing changed.
   - Otherwise, call `get_items(modified_after=<your stored Item timestamp>)`,
     `get_price_list(branch=..., modified_after=<your stored Item Price timestamp>)`, and
     `get_stock_levels(branch=..., modified_after=<your stored Bin timestamp>)`, paging until
     `has_more: false`. Update your stored timestamps to the last row's `modified` value each time.
   - Also call `get_deleted_items(since=<your stored Item timestamp>)` and remove/hide anything it
     returns.
3. Re-run a full unpaginated resync (step 1) periodically (e.g. weekly) as a reconciliation safety
   net, independent of the incremental sync — cheap insurance against any drift.

## 6. Limits & what's intentionally not built yet

- Every endpoint is rate-limited (see each section above) — keyed per source IP. A tight retry
  loop will get throttled with HTTP 429; back off and retry, don't hammer.
- Page size is capped at 1000 rows regardless of what you request.
- **Not built in this v1** (documented so you're not surprised, ask if you need one sooner):
  push/webhooks (this is poll-only for now), HTTP `ETag`/`If-Modified-Since` conditional requests,
  cursor-based pagination (current pagination is offset-based — safe for incremental
  `modified_after` syncs per §5, less so if you try to resume a stalled full page-walk after a lot
  of writes happened mid-walk), and a formal `/get_version` breaking-change contract. If this API
  evolves in a breaking way, you'll be told directly rather than via an in-band version check.

## 7. Getting help

This API is maintained by Nabeel / the aimatic backend team. Report anything that looks wrong
(unexpected duplicate rows beyond the one documented case, missing fields, wrong prices) directly
rather than silently working around it in your own sync code.
