"""Principal tagging on purchase documents for multi-company distributors.

Rules:
- Allowed Principals live on Supplier.custom_principals (no default).
- When the supplier has any allowed Principal, custom_principal is required
  and must be one of that list.
- When the supplier has none, custom_principal must stay blank.
- PO → PR → PI copies custom_principal when the child field is blank.
- If still blank, guess from Item.custom_principal when every line agrees,
  else from that supplier's prior tagged purchases of the same items.
"""

from __future__ import annotations

from collections import defaultdict

import frappe
from frappe import _

INTERNAL_SUPPLIER_PREFIXES = ("SIEZAL SUPERMARKET",)


def get_allowed_principals(supplier: str | None) -> list[str]:
	if not supplier:
		return []
	rows = frappe.get_all(
		"Supplier Principal",
		filters={"parent": supplier, "parenttype": "Supplier"},
		fields=["principal"],
		order_by="idx asc",
	)
	return [r.principal for r in rows if r.principal]


def is_internal_supplier(supplier: str | None) -> bool:
	name = (supplier or "").strip().upper()
	return any(name.startswith(prefix) for prefix in INTERNAL_SUPPLIER_PREFIXES)


def extract_principal_from_supplier_name(supplier: str | None) -> str | None:
	"""Parse trailing ``(PRINCIPAL)`` from supplier id/name when it matches Principal master."""
	if not supplier or is_internal_supplier(supplier):
		return None
	text = supplier.strip()
	if "(" not in text or not text.endswith(")"):
		# Also try supplier_name display when id has no paren.
		display = frappe.db.get_value("Supplier", supplier, "supplier_name") or ""
		text = display.strip()
		if "(" not in text or not text.endswith(")"):
			return None
	hint = text[text.rfind("(") + 1 : -1].strip()
	if not hint:
		return None
	if frappe.db.exists("Principal", hint):
		return hint
	# Case-insensitive Principal match
	matched = frappe.db.get_value("Principal", {"name": hint}, "name")
	if matched:
		return matched
	rows = frappe.get_all("Principal", filters={"name": ["like", hint]}, pluck="name", limit=2)
	if len(rows) == 1:
		return rows[0]
	return None


def ensure_supplier_principal(supplier: str, principal: str) -> bool:
	"""Add Principal to Supplier.custom_principals when missing. Returns True if inserted."""
	if not supplier or not principal:
		return False
	if is_internal_supplier(supplier):
		return False
	if not frappe.db.exists("Principal", principal):
		return False
	existing = get_allowed_principals(supplier)
	if principal in existing:
		return False
	doc = frappe.get_doc("Supplier", supplier)
	doc.append("custom_principals", {"principal": principal})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.save()
	return True


def guess_principal_from_supplier(supplier: str | None) -> str | None:
	"""Sole allow-list entry, else ``(PRINCIPAL)`` hint when allowed (or allow-list empty)."""
	if not supplier or is_internal_supplier(supplier):
		return None
	allowed = get_allowed_principals(supplier)
	if len(allowed) == 1:
		return allowed[0]
	hint = extract_principal_from_supplier_name(supplier)
	if not hint:
		return None
	if not allowed or hint in allowed:
		return hint
	return None


def validate_purchase_principal(doc, method=None):
	"""validate: enforce principal rules on PO / PR / PI."""
	# Last-chance auto-fill before the required check (items may have landed late).
	if not (getattr(doc, "custom_principal", None) or "").strip():
		_apply_guessed_principal(doc)

	allowed = get_allowed_principals(getattr(doc, "supplier", None))
	principal = (getattr(doc, "custom_principal", None) or "").strip()

	if allowed:
		if not principal:
			frappe.throw(_("Principal is required for supplier {0}.").format(frappe.bold(doc.supplier)))
		if principal not in allowed:
			frappe.throw(
				_("Principal {0} is not allowed for supplier {1}.").format(
					frappe.bold(principal), frappe.bold(doc.supplier)
				)
			)
		validate_item_principals(doc, principal)
		return

	if principal:
		frappe.throw(
			_(
				"Supplier {0} has no Principals configured. Clear Principal or "
				"add allowed Principals on the Supplier."
			).format(frappe.bold(doc.supplier))
		)


def validate_item_principals(doc, principal: str):
	"""Enforce approved Item mappings after the supplier rollout gate is on."""
	if not getattr(doc, "supplier", None):
		return
	# Code can be loaded briefly before migrate creates the rollout field.
	if not frappe.get_meta("Supplier").has_field("custom_enforce_item_principal"):
		return
	if not frappe.db.get_value("Supplier", doc.supplier, "custom_enforce_item_principal"):
		return

	item_codes = sorted({row.item_code for row in (getattr(doc, "items", None) or []) if row.item_code})
	if not item_codes:
		return
	mapped = dict(
		frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "custom_principal"],
			as_list=True,
		)
	)
	missing = [code for code in item_codes if not mapped.get(code)]
	mismatched = [code for code in item_codes if mapped.get(code) and mapped[code] != principal]
	if missing:
		frappe.throw(
			_("Principal is not mapped on Items: {0}").format(
				", ".join(frappe.bold(code) for code in missing)
			)
		)
	if mismatched:
		details = ", ".join(f"{code} ({mapped[code]})" for code in mismatched)
		frappe.throw(
			_("Item Principal must match document Principal {0}: {1}").format(frappe.bold(principal), details)
		)


def _first_linked_principal(doc, link_field: str, parent_doctype: str):
	seen = set()
	for row in getattr(doc, "items", None) or []:
		parent_name = row.get(link_field) if hasattr(row, "get") else getattr(row, link_field, None)
		if not parent_name or parent_name in seen:
			continue
		seen.add(parent_name)
		value = frappe.db.get_value(parent_doctype, parent_name, "custom_principal")
		if value:
			return value
	return None


def resolve_principal_for_receipt(doc) -> str | None:
	return _first_linked_principal(doc, "purchase_order", "Purchase Order")


def resolve_principal_for_invoice(doc) -> str | None:
	from_pr = _first_linked_principal(doc, "purchase_receipt", "Purchase Receipt")
	if from_pr:
		return from_pr
	return _first_linked_principal(doc, "purchase_order", "Purchase Order")


def guess_principal_from_items(doc) -> str | None:
	"""Return a Principal when every item line points to the same one.

	Priority:
	1. Approved Item.custom_principal (all lines mapped and equal)
	2. Unique prior tagged purchase history for this supplier+company+item set
	   (each item has exactly one historical Principal, and they all match)

	Returns None when mixed, incomplete, or no evidence — never invents a brand.
	"""
	item_codes = sorted({row.item_code for row in (getattr(doc, "items", None) or []) if row.item_code})
	if not item_codes:
		return None

	supplier = getattr(doc, "supplier", None)
	company = getattr(doc, "company", None)
	allowed = set(get_allowed_principals(supplier))

	mapped = {
		row.name: row.custom_principal
		for row in frappe.get_all(
			"Item",
			filters={"name": ["in", item_codes]},
			fields=["name", "custom_principal"],
		)
		if row.custom_principal
	}
	if len(mapped) == len(item_codes):
		principals = set(mapped.values())
		if len(principals) == 1:
			principal = next(iter(principals))
			if not allowed or principal in allowed:
				return principal
		return None

	if not supplier or not company:
		return None

	history = _historical_principals_by_item(company=company, supplier=supplier, item_codes=item_codes)
	resolved = []
	for code in item_codes:
		principals = history.get(code) or set()
		if len(principals) != 1:
			return None
		resolved.append(next(iter(principals)))
	if len(set(resolved)) != 1:
		return None
	principal = resolved[0]
	if allowed and principal not in allowed:
		return None
	return principal


def _historical_principals_by_item(company: str, supplier: str, item_codes: list[str]) -> dict[str, set[str]]:
	rows = frappe.db.sql(
		"""
		SELECT pri.item_code, pr.custom_principal AS principal
		FROM `tabPurchase Receipt` pr
		JOIN `tabPurchase Receipt Item` pri ON pri.parent = pr.name
		WHERE pr.company = %(company)s
		  AND pr.supplier = %(supplier)s
		  AND pr.docstatus = 1
		  AND IFNULL(pr.custom_principal, '') != ''
		  AND pri.item_code IN %(item_codes)s

		UNION

		SELECT pii.item_code, pi.custom_principal
		FROM `tabPurchase Invoice` pi
		JOIN `tabPurchase Invoice Item` pii ON pii.parent = pi.name
		WHERE pi.company = %(company)s
		  AND pi.supplier = %(supplier)s
		  AND pi.docstatus = 1
		  AND IFNULL(pi.custom_principal, '') != ''
		  AND pii.item_code IN %(item_codes)s
		""",
		{"company": company, "supplier": supplier, "item_codes": tuple(item_codes)},
		as_dict=True,
	)
	grouped = defaultdict(set)
	for row in rows:
		if row.item_code and row.principal:
			grouped[row.item_code].add(row.principal)
	return dict(grouped)


def guess_principal(doc) -> str | None:
	"""Best single Principal for a purchase doc without inventing mixed brands."""
	supplier = getattr(doc, "supplier", None)
	from_supplier = guess_principal_from_supplier(supplier)
	if from_supplier:
		return from_supplier
	return guess_principal_from_items(doc)


def _apply_guessed_principal(doc) -> str | None:
	if (getattr(doc, "custom_principal", None) or "").strip():
		return doc.custom_principal
	value = guess_principal(doc)
	if value:
		doc.custom_principal = value
	return value


def prefill_purchase_order_principal(doc, method=None):
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "custom_principal", None):
		return
	_apply_guessed_principal(doc)


def prefill_purchase_receipt_principal(doc, method=None):
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "custom_principal", None):
		return
	value = resolve_principal_for_receipt(doc)
	if value:
		doc.custom_principal = value
		return
	_apply_guessed_principal(doc)


def prefill_purchase_invoice_principal(doc, method=None):
	if getattr(doc, "docstatus", 0) != 0:
		return
	if getattr(doc, "custom_principal", None):
		return
	value = resolve_principal_for_invoice(doc)
	if value:
		doc.custom_principal = value
		return
	_apply_guessed_principal(doc)


def _prefill_mapped_principal(doc, source_doctype: str, source_name: str):
	if not doc or doc.get("custom_principal"):
		return doc
	value = frappe.db.get_value(source_doctype, source_name, "custom_principal")
	if value:
		doc.custom_principal = value
	return doc


def apply_principal_on_mapped_receipt(doc, source_name: str):
	return _prefill_mapped_principal(doc, "Purchase Order", source_name)


def apply_principal_on_mapped_invoice_from_order(doc, source_name: str):
	return _prefill_mapped_principal(doc, "Purchase Order", source_name)


def apply_principal_on_mapped_invoice_from_receipt(doc, source_name: str):
	doc = _prefill_mapped_principal(doc, "Purchase Receipt", source_name)
	if doc and doc.get("custom_principal"):
		return doc
	# PR may itself only inherit from PO — follow once via item links.
	po_names = frappe.db.sql(
		"""
		select distinct purchase_order
		from `tabPurchase Receipt Item`
		where parent = %s and ifnull(purchase_order, '') != ''
		""",
		source_name,
		pluck=True,
	)
	for po_name in po_names or []:
		value = frappe.db.get_value("Purchase Order", po_name, "custom_principal")
		if value:
			doc.custom_principal = value
			break
	return doc


@frappe.whitelist()
def guess_principal_for_purchase_doc(doctype: str, docname: str | None = None, doc: str | None = None):
	"""Desk helper: return the item-based Principal guess for the open form."""
	if doc:
		purchase_doc = frappe.parse_json(doc)
		purchase_doc = frappe.get_doc(purchase_doc)
	elif docname:
		purchase_doc = frappe.get_doc(doctype, docname)
	else:
		frappe.throw(_("Document is required."))
	linked = None
	if doctype == "Purchase Receipt":
		linked = resolve_principal_for_receipt(purchase_doc)
	elif doctype == "Purchase Invoice":
		linked = resolve_principal_for_invoice(purchase_doc)
	guessed = guess_principal_from_items(purchase_doc)
	return {"linked": linked, "guessed": guessed, "principal": linked or guessed}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_principal_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search: only Principals allowed on the selected Supplier."""
	supplier = (filters or {}).get("supplier")
	allowed = get_allowed_principals(supplier)
	if not allowed:
		return []

	return frappe.db.sql(
		"""
		select name, principal_name
		from `tabPrincipal`
		where disabled = 0
		  and name in %(allowed)s
		  and (name like %(txt)s or principal_name like %(txt)s)
		order by principal_name
		limit %(start)s, %(page_len)s
		""",
		{
			"allowed": tuple(allowed),
			"txt": f"%{txt}%",
			"start": int(start or 0),
			"page_len": int(page_len or 20),
		},
	)
