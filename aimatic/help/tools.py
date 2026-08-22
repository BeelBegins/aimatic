"""Allowlisted help tools — Desk URLs and topic lookup only. No ERP writes."""

from __future__ import annotations

import json

import frappe

from aimatic.help.retriever import retrieve_topics

TOOL_SPECS = [
	{
		"type": "function",
		"function": {
			"name": "search_help",
			"description": "Search curated Help Topics by query and optional module/doctype.",
			"parameters": {
				"type": "object",
				"properties": {
					"query": {"type": "string"},
					"module": {"type": "string"},
					"doctype": {"type": "string"},
				},
				"required": ["query"],
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "get_topic",
			"description": "Load one Help Topic by name (document name).",
			"parameters": {
				"type": "object",
				"properties": {"name": {"type": "string"}},
				"required": ["name"],
			},
		},
	},
	{
		"type": "function",
		"function": {
			"name": "list_related_doctypes",
			"description": "Return Desk deep-links for DocTypes related to a module or doctype.",
			"parameters": {
				"type": "object",
				"properties": {
					"module": {"type": "string"},
					"doctype": {"type": "string"},
				},
			},
		},
	},
]

_MODULE_LINKS = {
	"Accounts": [
		{"doctype": "Journal Entry", "url": "/app/journal-entry"},
		{"doctype": "Payment Entry", "url": "/app/payment-entry"},
		{"doctype": "Chart of Accounts", "url": "/app/chart-of-accounts"},
		{"doctype": "Sales Invoice", "url": "/app/sales-invoice"},
	],
	"Stock": [
		{"doctype": "Stock Entry", "url": "/app/stock-entry"},
		{"doctype": "Stock Reconciliation", "url": "/app/stock-reconciliation"},
		{"doctype": "Warehouse", "url": "/app/warehouse"},
	],
	"Item": [
		{"doctype": "Item", "url": "/app/item"},
		{"doctype": "Item Group", "url": "/app/item-group"},
	],
	"Price List": [
		{"doctype": "Price List", "url": "/app/price-list"},
		{"doctype": "Item Price", "url": "/app/item-price"},
	],
	"Selling": [
		{"doctype": "Sales Order", "url": "/app/sales-order"},
		{"doctype": "Customer", "url": "/app/customer"},
		{"doctype": "POS Invoice", "url": "/app/pos-invoice"},
	],
	"Buying": [
		{"doctype": "Purchase Order", "url": "/app/purchase-order"},
		{"doctype": "Purchase Receipt", "url": "/app/purchase-receipt"},
		{"doctype": "Purchase Invoice", "url": "/app/purchase-invoice"},
		{"doctype": "Supplier", "url": "/app/supplier"},
	],
	"Aimatic": [
		{"doctype": "Branch", "url": "/app/branch"},
		{"doctype": "Item Price Update Log", "url": "/app/item-price-update-log"},
		{"doctype": "Foodpanda Order Log", "url": "/app/foodpanda-order-log"},
		{"doctype": "Gift Voucher", "url": "/app/gift-voucher"},
	],
}


def _search_help(query: str, module: str | None = None, doctype: str | None = None) -> dict:
	topics = retrieve_topics(query or "", doctype=doctype, module=module, limit=5)
	return {
		"topics": [
			{"name": t["name"], "title": t["title"], "module": t["module"], "excerpt": (t["body"] or "")[:500]}
			for t in topics
		]
	}


def _get_topic(name: str) -> dict:
	if not name or not frappe.db.exists("Help Topic", name):
		return {"error": "Topic not found"}
	doc = frappe.get_doc("Help Topic", name)
	if not doc.enabled:
		return {"error": "Topic disabled"}
	return {
		"name": doc.name,
		"title": doc.title,
		"module": doc.module,
		"doctypes": doc.doctypes,
		"body": doc.body,
	}


def _list_related_doctypes(module: str | None = None, doctype: str | None = None) -> dict:
	from aimatic.help.prompt import DOCTYPE_TO_MODULE, infer_module

	mod = module or infer_module(doctype) or (DOCTYPE_TO_MODULE.get(doctype or "") if doctype else None)
	links = list(_MODULE_LINKS.get(mod or "", []))
	if doctype:
		slug = frappe.scrub(doctype).replace("_", "-")
		links.insert(0, {"doctype": doctype, "url": f"/app/{slug}"})
	# de-dupe by doctype
	seen = set()
	unique = []
	for row in links:
		key = row["doctype"]
		if key in seen:
			continue
		seen.add(key)
		unique.append(row)
	return {"module": mod, "links": unique}


TOOL_DISPATCH = {
	"search_help": lambda args: _search_help(
		args.get("query") or "",
		module=args.get("module"),
		doctype=args.get("doctype"),
	),
	"get_topic": lambda args: _get_topic(args.get("name") or ""),
	"list_related_doctypes": lambda args: _list_related_doctypes(
		module=args.get("module"),
		doctype=args.get("doctype"),
	),
}


def run_tool(name: str, arguments: str | dict | None) -> str:
	if name not in TOOL_DISPATCH:
		return json.dumps({"error": f"Unknown tool: {name}"})
	if isinstance(arguments, str):
		try:
			args = json.loads(arguments or "{}")
		except json.JSONDecodeError:
			args = {}
	else:
		args = arguments or {}
	try:
		result = TOOL_DISPATCH[name](args)
	except Exception as exc:
		frappe.log_error(title="Help tool failed", message=f"{name}: {exc}")
		result = {"error": "Tool failed"}
	return json.dumps(result, ensure_ascii=False, default=str)
