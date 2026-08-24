"""System prompt and context formatting for the Desk Help float."""

from __future__ import annotations

import json
from typing import Any

BLOCKED_SETUP_TOPICS = (
	"FBR Integration Settings",
	"Foodpanda Settings",
	"AI Integration Settings",
	"OAuth Client",
	"device enrollment",
	"cashier PIN",
	"API keys",
	"tokens",
	"certificates",
	"webhook secrets",
)

DOCTYPE_TO_MODULE = {
	"Journal Entry": "Accounts",
	"Payment Entry": "Accounts",
	"Account": "Accounts",
	"Sales Invoice": "Accounts",
	"Purchase Invoice": "Buying",
	"Purchase Order": "Buying",
	"Purchase Receipt": "Buying",
	"Supplier": "Buying",
	"Stock Entry": "Stock",
	"Stock Reconciliation": "Stock",
	"Warehouse": "Stock",
	"Item": "Item",
	"Item Group": "Item",
	"Item Price": "Price List",
	"Price List": "Price List",
	"Sales Order": "Selling",
	"Customer": "Selling",
	"POS Invoice": "Selling",
	"POS Profile": "Selling",
	"Branch": "Aimatic",
	"Item Price Update Log": "Aimatic",
	"Gift Voucher": "Aimatic",
	"Foodpanda Order Log": "Aimatic",
	"Foodpanda Outlet": "Aimatic",
}


def infer_module(doctype: str | None, message: str = "") -> str | None:
	if doctype and doctype in DOCTYPE_TO_MODULE:
		return DOCTYPE_TO_MODULE[doctype]
	text = (message or "").lower()
	hints = (
		(("journal", "payment entry", "chart of accounts", "gl ", "ledger"), "Accounts"),
		(("stock entry", "warehouse", "reconciliation", "inventory"), "Stock"),
		(("item group", "barcode", "uom"), "Item"),
		(("price list", "item price", "mrp", "selling price"), "Price List"),
		(("sales order", "customer", "quotation"), "Selling"),
		(("purchase order", "purchase receipt", "supplier"), "Buying"),
		(("foodpanda", "gift voucher", "shelf price", "branch"), "Aimatic"),
	)
	for keywords, module in hints:
		if any(k in text for k in keywords):
			return module
	if "item" in text:
		return "Item"
	return None


def build_system_prompt(context: dict[str, Any] | None, topic_snippets: list[dict]) -> str:
	ctx = context or {}
	blocked = ", ".join(BLOCKED_SETUP_TOPICS)
	parts = [
		"You are Aimatic Help, an in-Desk ERPNext tutor for retail users.",
		"Explain how to use screens, fields, and processes in clear step-by-step language.",
		"Prefer the curated Help Topic snippets below when they match the question.",
		"Include Desk deep-links as markdown when useful, e.g. [/app/item](/app/item) or [/app/list/Item](/app/list/Item).",
		"If frappe.help documentation URLs are provided in context, cite them.",
		"Never execute or claim to have submitted, cancelled, or changed any document.",
		"Never ask for or explain how to obtain API keys, tokens, certificates, PINs, OAuth secrets, or device enrollment codes.",
		f"Refuse setup help for: {blocked}. Say an administrator configures those.",
		"If unsure, say what to check next or suggest Ctrl+K (Awesome Bar) to find a DocType.",
		"Keep answers concise (usually under 12 short steps).",
	]
	doctype = (ctx.get("doctype") or "").strip()
	route = (ctx.get("route") or "").strip()
	module = (ctx.get("module") or "").strip()
	if doctype or route or module:
		parts.append(
			"Current Desk context: "
			+ json.dumps(
				{
					"doctype": doctype or None,
					"route": route or None,
					"module": module or None,
					"docname": ctx.get("docname") or None,
					"meta_description": ctx.get("meta_description") or None,
					"documentation_url": ctx.get("documentation_url") or None,
					"help_links": ctx.get("help_links") or [],
				},
				ensure_ascii=False,
			)
		)
	if topic_snippets:
		parts.append("Curated help snippets (use these first):")
		for snip in topic_snippets:
			parts.append(
				f"### {snip.get('title')}\nModule: {snip.get('module')}\n{snip.get('body')}"
			)
	return "\n\n".join(parts)
