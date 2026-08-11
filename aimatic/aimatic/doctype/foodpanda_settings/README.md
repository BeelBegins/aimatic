# Foodpanda Settings

Site-wide Partner API configuration for Aimatic’s Foodpanda integration.

**Goal:** keep each enabled Foodpanda outlet synchronized with ERPNext — prices,
stock/availability, active/inactive, and (when enabled) inbound orders — without
manual portal uploads.

Full status (done / left / how sync works):
[`docs/foodpanda-synchronized-outlet.md`](../../../../../docs/foodpanda-synchronized-outlet.md)

---

## What this page is for

Fill credentials and switches once per site. Per-branch work (vendor ID, catalog
sync, mapping, push) lives on **Foodpanda Outlet**, **Foodpanda Catalog Sheet**,
and the **Catalog Console**.

| Field | Purpose |
|---|---|
| **Enabled** | Master switch for Partner API calls from this site |
| **API Host** | Partner API origin (live Partner host; no separate catalog sandbox host) |
| **Chain ID** | Path `{chain_id}` on catalog / order / outlet APIs |
| **Client ID / Client Secret** | OAuth2 client-credentials (secret is never exported as a fixture) |
| **Webhook Secret** | Exact `Authorization` value Foodpanda sends on order and assortment callbacks |
| **Catalog Locale** | Locale key for localized catalog text (e.g. `en_PK`) |
| **Allow Product Creation** | Beta POST “add product”. Keep off unless Foodpanda has enabled creation for your test/live vendor. Steady-state sync maps existing portal SKUs by barcode and PUTs price/stock |
| **Request Timeout / Retries / Verify SSL** | HTTP client behaviour |

---

## Setup checklist

1. Enable **Foodpanda Settings** and save Chain ID + OAuth credentials from Foodpanda.
2. Set **Webhook Secret** to the same static Authorization value configured in the Foodpanda Vendor Portal.
3. On this form use **Show webhook URLs** and register both URLs in the Vendor Portal (order + assortment / catalog job callbacks).
4. Create a **Foodpanda Outlet** per branch (`vendor_id`, catalog sync, optional order ingestion).
5. Import catalog → map by barcode → push prices/stock from **Catalog Sheet** or Console.
6. Prefer Partner sync over Branch Price Sheet CSV/SFTP once mapping is healthy.

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
