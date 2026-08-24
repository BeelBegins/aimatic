"""Document-flow graph for purchase and sales cycles.

Walks known ERPNext link fields (item against-docs, return_against, Payment
Entry Reference) in both directions and returns a rooted tree for the UI.
"""

from __future__ import annotations

from collections import defaultdict, deque

import frappe
from frappe.utils import getdate

# DocTypes that show the Relationship Manager on form open.
SUPPORTED_DOCTYPES = frozenset(
	{
		"Material Request",
		"Purchase Order",
		"Purchase Receipt",
		"Purchase Invoice",
		"Quotation",
		"Sales Order",
		"Delivery Note",
		"Sales Invoice",
		"Payment Entry",
		"Landed Cost Voucher",
	}
)

# Max nodes returned for one open form (keeps Desk responsive).
_MAX_NODES = 150

# Edge: how child documents point at a parent.
# kind:
#   item_link  — child Item row field = parent name (optional doctype field)
#   return     — child.return_against = parent name
#   payment    — Payment Entry Reference points at parent
#   lcv        — Landed Cost Voucher Item.receipt_document = parent
_EDGES: tuple[dict, ...] = (
	# Purchase
	{
		"parent": "Material Request",
		"child": "Purchase Order",
		"kind": "item_link",
		"child_table": "Purchase Order Item",
		"field": "material_request",
	},
	{
		"parent": "Purchase Order",
		"child": "Purchase Receipt",
		"kind": "item_link",
		"child_table": "Purchase Receipt Item",
		"field": "purchase_order",
	},
	{
		"parent": "Purchase Order",
		"child": "Purchase Invoice",
		"kind": "item_link",
		"child_table": "Purchase Invoice Item",
		"field": "purchase_order",
	},
	{
		"parent": "Purchase Receipt",
		"child": "Purchase Invoice",
		"kind": "item_link",
		"child_table": "Purchase Invoice Item",
		"field": "purchase_receipt",
	},
	{
		"parent": "Purchase Receipt",
		"child": "Landed Cost Voucher",
		"kind": "lcv",
	},
	{
		"parent": "Purchase Order",
		"child": "Payment Entry",
		"kind": "payment",
	},
	{
		"parent": "Purchase Invoice",
		"child": "Payment Entry",
		"kind": "payment",
	},
	{
		"parent": "Purchase Receipt",
		"child": "Purchase Receipt",
		"kind": "return",
	},
	{
		"parent": "Purchase Invoice",
		"child": "Purchase Invoice",
		"kind": "return",
	},
	# Sales
	{
		"parent": "Quotation",
		"child": "Sales Order",
		"kind": "item_link",
		"child_table": "Sales Order Item",
		"field": "prevdoc_docname",
	},
	{
		"parent": "Sales Order",
		"child": "Delivery Note",
		"kind": "item_link",
		"child_table": "Delivery Note Item",
		"field": "against_sales_order",
	},
	{
		"parent": "Sales Order",
		"child": "Sales Invoice",
		"kind": "item_link",
		"child_table": "Sales Invoice Item",
		"field": "sales_order",
	},
	{
		"parent": "Delivery Note",
		"child": "Sales Invoice",
		"kind": "item_link",
		"child_table": "Sales Invoice Item",
		"field": "delivery_note",
	},
	{
		"parent": "Sales Order",
		"child": "Payment Entry",
		"kind": "payment",
	},
	{
		"parent": "Sales Invoice",
		"child": "Payment Entry",
		"kind": "payment",
	},
	{
		"parent": "Sales Invoice",
		"child": "Sales Invoice",
		"kind": "return",
	},
	{
		"parent": "Delivery Note",
		"child": "Delivery Note",
		"kind": "return",
	},
)


def _key(doctype: str, name: str) -> tuple[str, str]:
	return (doctype, name)


def _status_label(docstatus: int, is_return: int = 0) -> str:
	base = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(int(docstatus or 0), "Unknown")
	if int(is_return or 0):
		return f"{base} · Return"
	return base


def _meta_fields(doctype: str) -> list[str]:
	fields = ["name", "docstatus", "modified"]
	meta = frappe.get_meta(doctype)
	for candidate in (
		"posting_date",
		"transaction_date",
		"schedule_date",
		"due_date",
		"grand_total",
		"rounded_total",
		"paid_amount",
		"status",
		"is_return",
		"supplier",
		"customer",
		"company",
		"return_against",
		"title",
	):
		if meta.has_field(candidate):
			fields.append(candidate)
	return fields


def _load_node(doctype: str, name: str) -> dict | None:
	if not name or not frappe.db.exists(doctype, name):
		return None
	if not frappe.has_permission(doctype, "read", doc=name):
		return {
			"doctype": doctype,
			"name": name,
			"restricted": 1,
			"status": "Restricted",
			"label": f"{doctype} · {name}",
		}

	row = frappe.db.get_value(doctype, name, _meta_fields(doctype), as_dict=True)
	if not row:
		return None

	date_val = (
		row.get("posting_date")
		or row.get("transaction_date")
		or row.get("schedule_date")
		or row.get("due_date")
	)
	amount = row.get("grand_total") or row.get("rounded_total") or row.get("paid_amount")
	is_return = int(row.get("is_return") or 0)

	return {
		"doctype": doctype,
		"name": name,
		"docstatus": int(row.get("docstatus") or 0),
		"status": _status_label(row.get("docstatus"), is_return),
		"doc_status": row.get("status"),
		"date": str(getdate(date_val)) if date_val else None,
		"amount": amount,
		"party": row.get("supplier") or row.get("customer"),
		"is_return": is_return,
		"return_against": row.get("return_against"),
		"restricted": 0,
		"label": f"{doctype} · {name}",
	}


def _distinct_parents(child_table: str, field: str, parent_name: str) -> list[str]:
	rows = frappe.get_all(
		child_table,
		filters={field: parent_name},
		fields=["parent"],
		distinct=True,
		limit=_MAX_NODES,
	)
	return [r.parent for r in rows if r.parent]


def _distinct_field_values(child_table: str, field: str, parent_names: list[str]) -> list[str]:
	if not parent_names:
		return []
	rows = frappe.get_all(
		child_table,
		filters={"parent": ["in", parent_names], field: ["is", "set"]},
		fields=[field],
		distinct=True,
		limit=_MAX_NODES,
	)
	return [r.get(field) for r in rows if r.get(field)]


def _children_via_item_link(edge: dict, parent_name: str) -> list[str]:
	return _distinct_parents(edge["child_table"], edge["field"], parent_name)


def _parents_via_item_link(edge: dict, child_name: str) -> list[str]:
	return _distinct_field_values(edge["child_table"], edge["field"], [child_name])


def _children_via_return(doctype: str, parent_name: str) -> list[str]:
	rows = frappe.get_all(
		doctype,
		filters={"return_against": parent_name},
		fields=["name"],
		limit=_MAX_NODES,
	)
	return [r.name for r in rows]


def _parents_via_return(doctype: str, child_name: str) -> list[str]:
	against = frappe.db.get_value(doctype, child_name, "return_against")
	return [against] if against else []


def _children_via_payment(parent_doctype: str, parent_name: str) -> list[str]:
	rows = frappe.get_all(
		"Payment Entry Reference",
		filters={"reference_doctype": parent_doctype, "reference_name": parent_name},
		fields=["parent"],
		distinct=True,
		limit=_MAX_NODES,
	)
	return [r.parent for r in rows if r.parent]


def _parents_via_payment(payment_name: str) -> list[tuple[str, str]]:
	rows = frappe.get_all(
		"Payment Entry Reference",
		filters={"parent": payment_name, "reference_name": ["is", "set"]},
		fields=["reference_doctype", "reference_name"],
		limit=_MAX_NODES,
	)
	out = []
	for r in rows:
		if r.reference_doctype and r.reference_name:
			out.append((r.reference_doctype, r.reference_name))
	return out


def _children_via_lcv(parent_doctype: str, parent_name: str) -> list[str]:
	rows = frappe.get_all(
		"Landed Cost Purchase Receipt",
		filters={"receipt_document_type": parent_doctype, "receipt_document": parent_name},
		fields=["parent"],
		distinct=True,
		limit=_MAX_NODES,
	)
	return [r.parent for r in rows if r.parent]


def _parents_via_lcv(lcv_name: str) -> list[tuple[str, str]]:
	rows = frappe.get_all(
		"Landed Cost Purchase Receipt",
		filters={"parent": lcv_name, "receipt_document": ["is", "set"]},
		fields=["receipt_document_type", "receipt_document"],
		limit=_MAX_NODES,
	)
	out = []
	for r in rows:
		if r.receipt_document_type and r.receipt_document:
			out.append((r.receipt_document_type, r.receipt_document))
	return out


def _neighbors(doctype: str, name: str) -> list[tuple[str, str, str]]:
	"""Return related (doctype, name, direction) where direction is parent|child."""
	related: list[tuple[str, str, str]] = []

	for edge in _EDGES:
		kind = edge["kind"]
		parent_dt = edge["parent"]
		child_dt = edge["child"]

		if kind == "item_link":
			if doctype == parent_dt:
				for child_name in _children_via_item_link(edge, name):
					related.append((child_dt, child_name, "child"))
			if doctype == child_dt:
				for parent_name in _parents_via_item_link(edge, name):
					related.append((parent_dt, parent_name, "parent"))

		elif kind == "return":
			if doctype == parent_dt:
				for child_name in _children_via_return(child_dt, name):
					related.append((child_dt, child_name, "child"))
			if doctype == child_dt:
				for parent_name in _parents_via_return(doctype, name):
					related.append((parent_dt, parent_name, "parent"))

		elif kind == "payment":
			if doctype == parent_dt:
				for pe in _children_via_payment(parent_dt, name):
					related.append(("Payment Entry", pe, "child"))
			if doctype == "Payment Entry":
				for pref_dt, pref_name in _parents_via_payment(name):
					if pref_dt == parent_dt:
						related.append((pref_dt, pref_name, "parent"))

		elif kind == "lcv":
			if doctype == parent_dt:
				for lcv in _children_via_lcv(parent_dt, name):
					related.append(("Landed Cost Voucher", lcv, "child"))
			if doctype == "Landed Cost Voucher":
				for pref_dt, pref_name in _parents_via_lcv(name):
					if pref_dt == parent_dt:
						related.append((pref_dt, pref_name, "parent"))

	return related


# When a doc has multiple parents in the flow (e.g. PI linked from both PO and
# PR), keep a single preferred parent so the tree does not duplicate nodes.
_PARENT_PRIORITY: dict[str, tuple[str, ...]] = {
	"Purchase Receipt": ("Purchase Order", "Material Request"),
	"Purchase Invoice": ("Purchase Receipt", "Purchase Order"),
	"Payment Entry": ("Purchase Invoice", "Sales Invoice", "Purchase Order", "Sales Order"),
	"Landed Cost Voucher": ("Purchase Receipt",),
	"Sales Order": ("Quotation",),
	"Delivery Note": ("Sales Order",),
	"Sales Invoice": ("Delivery Note", "Sales Order"),
}


def _pick_preferred_parent(
	child_key: tuple[str, str], parent_keys: set[tuple[str, str]]
) -> tuple[str, str] | None:
	if not parent_keys:
		return None
	if len(parent_keys) == 1:
		return next(iter(parent_keys))
	priority = _PARENT_PRIORITY.get(child_key[0], ())
	by_doctype = {p[0]: p for p in parent_keys}
	for dt in priority:
		if dt in by_doctype:
			return by_doctype[dt]
	return sorted(parent_keys, key=lambda k: (k[0], k[1]))[0]


def _collapse_to_tree_edges(
	present: set[tuple[str, str]],
	parents_of: dict[tuple[str, str], set[tuple[str, str]]],
) -> dict[tuple[str, str], set[tuple[str, str]]]:
	"""One parent per child (preferred), used only for nested tree rendering."""
	children_of: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
	for child in present:
		preferred = _pick_preferred_parent(child, parents_of[child] & present)
		if preferred:
			children_of[preferred].add(child)
	return children_of


def build_relationship_tree(doctype: str, name: str) -> dict:
	"""BFS outward from the open document; return rooted tree + flat nodes."""
	if doctype not in SUPPORTED_DOCTYPES:
		frappe.throw(f"Relationship Manager does not support {doctype}")

	frappe.has_permission(doctype, "read", doc=name, throw=True)

	seed = _key(doctype, name)
	nodes: dict[tuple[str, str], dict] = {}
	# child_key -> set(parent_key) for tree assembly
	parents_of: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)

	queue: deque[tuple[str, str]] = deque([seed])
	seen: set[tuple[str, str]] = {seed}

	while queue and len(nodes) + len(queue) < _MAX_NODES:
		dt, nm = queue.popleft()
		node = _load_node(dt, nm)
		if not node:
			continue
		nodes[_key(dt, nm)] = node

		if node.get("restricted"):
			continue

		for rel_dt, rel_nm, direction in _neighbors(dt, nm):
			rel = _key(rel_dt, rel_nm)
			if direction == "child":
				parents_of[rel].add(_key(dt, nm))
			else:
				parents_of[_key(dt, nm)].add(rel)

			if rel not in seen:
				seen.add(rel)
				queue.append(rel)

	# Load any edge endpoints not yet loaded (cap)
	for key in list(seen):
		if key not in nodes and len(nodes) < _MAX_NODES:
			node = _load_node(key[0], key[1])
			if node:
				nodes[key] = node

	present = set(nodes.keys())
	children_of = _collapse_to_tree_edges(present, parents_of)

	# Prefer roots: nodes in the connected set with no preferred parent.
	child_keys = {c for kids in children_of.values() for c in kids}
	roots = sorted(
		[k for k in present if k not in child_keys],
		key=lambda k: (k[0], k[1]),
	)
	if not roots and seed in present:
		roots = [seed]

	def build_branch(key: tuple[str, str], trail: set[tuple[str, str]]) -> dict | None:
		node = nodes.get(key)
		if not node:
			return None
		item = {
			**node,
			"current": 1 if key == seed else 0,
			"children": [],
		}
		if key in trail:
			return item
		next_trail = trail | {key}
		for child_key in sorted(children_of.get(key, set()), key=lambda k: (k[0], k[1])):
			branch = build_branch(child_key, next_trail)
			if branch:
				item["children"].append(branch)
		return item

	tree = []
	for root in roots:
		branch = build_branch(root, set())
		if branch:
			tree.append(branch)

	return {
		"doctype": doctype,
		"name": name,
		"tree": tree,
		"count": len(nodes),
		"truncated": len(nodes) >= _MAX_NODES,
	}
