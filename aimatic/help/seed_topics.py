"""Seed curated Help Topic rows for the Desk Help float."""

from __future__ import annotations

SEED_TOPICS = [
	{
		"title": "Create and maintain an Item",
		"module": "Item",
		"doctypes": "Item, Item Group",
		"tags": "item barcode uom group stock",
		"priority": 10,
		"starter_questions": "How do I create an Item?\nWhere do I set barcodes and UOM?\nWhat is Item Group used for?",
		"body": """## Create an Item
1. Open [/app/item/new](/app/item/new) (or Awesome Bar → Item → New).
2. Enter **Item Code**, **Item Name**, and **Item Group**.
3. Set **Stock UOM**. Add alternate UOMs in the UOM Conversion table if needed.
4. For retail barcodes, add rows under **Barcodes**.
5. Save. Enable **Maintain Stock** only for inventory items.

## Tips
- Use Item Group for reporting and loyalty weighting.
- Do not invent prices on the Item alone — selling prices live on **Item Price** / **Price List**.
""",
	},
	{
		"title": "Selling Price List and Item Price",
		"module": "Price List",
		"doctypes": "Price List, Item Price",
		"tags": "price list item price selling mrp shelf",
		"priority": 10,
		"starter_questions": "Where do I set selling prices?\nHow does Price List work?\nHow do I open Item Price?",
		"body": """## Price List
1. Open [/app/price-list](/app/price-list).
2. Each Price List is Buying or Selling. Branch shelf prices use Selling lists.
3. Open [/app/item-price](/app/item-price) to set **Item + Price List + Rate**.

## Mechanics
- POS and invoices pick rates from the Price List on the POS Profile / transaction.
- Aimatic may also maintain branch and Foodpanda lists — ask an admin for which list your branch uses.
- Use **Price Check** workspace tools for branch verification when available.
""",
	},
	{
		"title": "Stock Entry and warehouses",
		"module": "Stock",
		"doctypes": "Stock Entry, Warehouse, Stock Reconciliation",
		"tags": "stock entry transfer material issue receipt warehouse",
		"priority": 10,
		"starter_questions": "How do I do a Stock Entry?\nHow do I transfer stock between warehouses?\nWhat is Stock Reconciliation?",
		"body": """## Stock Entry
1. Open [/app/stock-entry/new](/app/stock-entry/new).
2. Pick **Stock Entry Type** (Material Transfer, Material Issue, Material Receipt, etc.).
3. Add items with qty. Set **Source** / **Target Warehouse** as required by the type.
4. Save, then **Submit** when quantities are correct.

## Stock Reconciliation
Use [/app/stock-reconciliation](/app/stock-reconciliation) to set counted stock to a known quantity. Submit carefully — it posts stock ledger.

## Tip
Use Ctrl+K to jump to Stock Balance or Stock Ledger reports.
""",
	},
	{
		"title": "Payment Entry and Journal Entry basics",
		"module": "Accounts",
		"doctypes": "Payment Entry, Journal Entry, Account, Sales Invoice",
		"tags": "payment entry journal entry accounts receive pay",
		"priority": 10,
		"starter_questions": "How do I record a Payment Entry?\nWhen do I use Journal Entry?\nHow do I open Chart of Accounts?",
		"body": """## Payment Entry
1. Open [/app/payment-entry/new](/app/payment-entry/new).
2. Choose **Receive** (customer) or **Pay** (supplier).
3. Select party, paid amount, and Mode of Payment / account.
4. Allocate against outstanding invoices if shown, then Save and Submit.

## Journal Entry
Use [/app/journal-entry](/app/journal-entry) for manual accounting adjustments (debit/credit accounts must balance).

## Chart of Accounts
Browse accounts at [/app/chart-of-accounts](/app/chart-of-accounts).
""",
	},
	{
		"title": "Purchase Order to Receipt to Invoice",
		"module": "Buying",
		"doctypes": "Purchase Order, Purchase Receipt, Purchase Invoice, Supplier",
		"tags": "purchase order receipt invoice supplier buying",
		"priority": 20,
		"starter_questions": "How do I create a Purchase Order?\nHow do I receive stock from a PO?\nHow do I make a Purchase Invoice?",
		"body": """## Happy path
1. Create **Supplier** if needed: [/app/supplier](/app/supplier).
2. **Purchase Order** [/app/purchase-order/new](/app/purchase-order/new) — items, rates, taxes → Submit.
3. **Purchase Receipt** from the PO (Create → Purchase Receipt) when goods arrive → Submit (stock in).
4. **Purchase Invoice** from PO/PR for accounts payable → Submit.

Shelf / Foodpanda selling-price updates may run from Purchase Receipt on Aimatic sites — that is automatic when configured; you do not enter API credentials here.
""",
	},
	{
		"title": "Sales Order and Sales Invoice",
		"module": "Selling",
		"doctypes": "Sales Order, Sales Invoice, Customer, POS Invoice",
		"tags": "sales order invoice customer pos",
		"priority": 20,
		"starter_questions": "How do I create a Sales Order?\nHow do I make a Sales Invoice?\nWhere do I review POS Invoices?",
		"body": """## Sales Order
1. [/app/sales-order/new](/app/sales-order/new) — Customer, items, rates → Save → Submit.

## Sales Invoice
Create from Order or [/app/sales-invoice/new](/app/sales-invoice/new). Submit posts accounts (and stock if update stock is on).

## POS Invoice
Terminal sales appear as [/app/pos-invoice](/app/pos-invoice) for Desk review. Cashier operations happen on the POS app — this Help float does not control the terminal.
""",
	},
	{
		"title": "Aimatic ops pointers (no credentials)",
		"module": "Aimatic",
		"doctypes": "Branch, Item Price Update Log, Foodpanda Order Log, Gift Voucher",
		"tags": "branch foodpanda gift voucher price update",
		"priority": 30,
		"starter_questions": "What is Item Price Update Log?\nWhere do I see Foodpanda orders?\nHow do gift vouchers work at a high level?",
		"body": """## Safe pointers
- **Branch** ties warehouses and price lists for a store: [/app/branch](/app/branch).
- **Item Price Update Log** shows shelf-price propagation status: [/app/item-price-update-log](/app/item-price-update-log).
- **Foodpanda Order Log** monitors inbound orders: [/app/foodpanda-order-log](/app/foodpanda-order-log). Catalog/API credentials are admin-only — not configured here.
- **Gift Voucher** issuance/redemption follows company criteria; open [/app/gift-voucher](/app/gift-voucher) for records.

Tax compliance submission settings are administrator-owned. Ask a System Manager — do not paste tokens or certificates into chat.
""",
	},
	{
		"title": "Finding your way in Desk",
		"module": "General",
		"doctypes": "",
		"tags": "awesome bar search navigation help",
		"priority": 40,
		"starter_questions": "How do I find a DocType quickly?\nWhat is Awesome Bar?",
		"body": """## Navigation
- Press **Ctrl+K** (Cmd+K) for Awesome Bar — jump to any DocType, report, or page.
- Press **Ctrl+G** for Global Search across documents.
- Use the Aimatic workspace tiles for Price Check, vendor tools, and AI reporting (separate from this Help chat).
- Navbar **Help** may include documentation links for the current page.
""",
	},
]
