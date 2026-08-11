import frappe
from frappe import _
from frappe.utils import flt

from aimatic.barcode_utils import barcode_variants
from aimatic.shelf_pricing.api import get_current_branch_sale_price

# Dedicated kiosk role only. System Manager kept for admin testing.
# Do NOT grant Item/Bin/Stock report rights to Price Check — the lookup uses
# db reads after this role gate so staff cannot open Item/Stock lists.
_ALLOWED_ROLES = {"Price Check", "System Manager"}


def _require_price_check_access():
	if not _ALLOWED_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(
			_("You need the Price Check role to look up selling prices."),
			frappe.PermissionError,
		)


def _resolve_item_codes(barcode: str) -> list[str]:
	"""Resolve Item parents for a scanned barcode without Item DocPerm.

	Uses db.sql (not get_all) so Price Check kiosk permission hooks on Item /
	Item Barcode cannot empty the result set. Tries GTIN padding variants and
	case-insensitive match — shelf scanners rarely match the stored form 1:1.
	"""
	barcode = (barcode or "").strip()
	if not barcode:
		frappe.throw(_("Barcode is required"))

	variants = barcode_variants(barcode)
	item_codes: list[str] = []

	if variants:
		rows = frappe.db.sql(
			"""
			SELECT parent, barcode, idx
			FROM `tabItem Barcode`
			WHERE barcode IN %(variants)s
			ORDER BY parent ASC, idx ASC
			""",
			{"variants": variants},
		)
		item_codes = [r[0] for r in rows]

	if not item_codes:
		# Case-only mismatch (e.g. typed s5 vs stored S5)
		rows = frappe.db.sql(
			"""
			SELECT parent
			FROM `tabItem Barcode`
			WHERE LOWER(barcode) = LOWER(%s)
			ORDER BY parent ASC, idx ASC
			LIMIT 100
			""",
			(barcode,),
		)
		item_codes = [r[0] for r in rows]

	if not item_codes and frappe.db.exists("Item", barcode):
		item_codes = [barcode]

	return list(dict.fromkeys(item_codes))


@frappe.whitelist()
def lookup_price_by_barcode(barcode: str, branch: str):
	"""Read-only barcode -> branch selling price lookup for the Price Check
	console. Reuses get_current_branch_sale_price for rate resolution.
	"""
	_require_price_check_access()

	branch = (branch or "").strip()
	if not branch:
		frappe.throw(_("Select a branch first."))

	item_codes = _resolve_item_codes(barcode)
	if not item_codes:
		return {"found": False}

	items = []
	for item_code in item_codes:
		item = frappe.db.get_value(
			"Item", item_code, ["item_name", "disabled", "custom_mrp"], as_dict=True
		)
		if not item or item.disabled:
			continue

		price = get_current_branch_sale_price(item_code, branch)
		items.append(
			{
				"item_code": item_code,
				"item_name": item.item_name,
				"rate": flt(price.get("rate")),
				"mrp": flt(item.custom_mrp),
			}
		)

	if not items:
		return {"found": False}

	return {"found": True, "items": items}
