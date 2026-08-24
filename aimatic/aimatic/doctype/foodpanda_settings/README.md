# Foodpanda Settings

Site-wide Partner API configuration for Aimatic’s Foodpanda integration.

**Goal:** keep each enabled Foodpanda outlet synchronized with ERPNext — prices,
stock/availability, active/inactive, and (when enabled) inbound orders — without
manual portal uploads.

Full status (done / left / how sync works):
[`docs/foodpanda-synchronized-outlet.md`](../../../../../docs/foodpanda-synchronized-outlet.md)

---

## What this page is for

Fill Partner API credentials once per site. Shared SFTP host/user/password
also live here. Per-branch work (chain ID, vendor ID, catalog sync, mapping,
push, SFTP enable/schedule) lives on **Foodpanda Outlet**, **Foodpanda Catalog
Sheet**, and the **Catalog Console**.

| Field | Purpose |
|---|---|
| **Enabled** | Master switch for Partner API calls from this site |
| **API Host** | Partner API origin (live Partner host; no separate catalog sandbox host) |
| **Default Chain ID** | Fallback `{chain_id}` when a Foodpanda Outlet has none. Each branch can have its own chain — set Chain ID on the outlet |
| **Client ID / Client Secret** | OAuth2 client-credentials (secret is never exported as a fixture) |
| **Webhook Secret** | Exact `Authorization` value Foodpanda sends on order and assortment callbacks |
| **Catalog Locale** | Locale key for localized catalog text (e.g. `en_PK`) |
| **Allow Product Creation** | Beta POST “add product”. Keep off unless Foodpanda has enabled creation for your test/live vendor. Steady-state sync maps existing portal SKUs by barcode and PUTs price/stock |
| **Request Timeout / Retries / Verify SSL** | HTTP client behaviour |
| **SFTP Host / Port / Username / Password** | Shared Foodpanda vendor-automation SFTP. Port is always 22 |
| **SFTP Remote Path** | Usually `Catalog` for catalog-only (double-file) uploads |
| **SFTP Filename Prefix** | Must match Vendor Portal → Integrations. File is `{prefix}_{vendor_id}.csv` |

---

## Setup checklist

1. Enable **Foodpanda Settings** and save OAuth credentials from Foodpanda.
2. Create a **Foodpanda Outlet** per branch (`chain_id`, `vendor_id`, catalog sync, optional order ingestion). Default Chain ID on Settings is only a fallback.
3. Set **Webhook Secret** to the same static Authorization value configured in the Foodpanda Vendor Portal.
4. On this form use **Show webhook URLs** and register both URLs in the Vendor Portal (order + assortment / catalog job callbacks).
5. Import catalog → map by barcode → push prices/stock from **Catalog Sheet** or Console.
6. For SFTP fallback: fill SFTP host/user/password here, set prefix to match Vendor Portal, enable schedule on each Outlet. Filename is `{prefix}_{vendor_id}.csv`.
7. Prefer Partner sync over Branch Price Sheet CSV/SFTP once mapping is healthy.

---

## Desk tools (after settings)

- **Foodpanda Outlet** — per-branch vendor, sync flags, catalog actions
- **Foodpanda Catalog Sheet** — edit Foodpanda prices and Portal Active; **Apply & push to Foodpanda**
- **foodpanda-catalog-console** — update prices & stock; refresh product links
- **Branch Price Sheet** — local Foodpanda Price List only (CSV/SFTP fallback)

---

## Safety

- Never put client secrets, webhook secrets, or tokens in code, fixtures, commits, or chat logs.
- Match catalog and order lines by **barcode / mapped Foodpanda SKU**, never assume Item Code equals the portal SKU.
- Production sites: backup, explicit approval, and rollback before first live push or order ingestion.
