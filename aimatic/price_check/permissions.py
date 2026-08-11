import frappe

# Pure price-check kiosk = Price Check plus Frappe's automatic roles only.
# Anyone with Stock/Sales/System Manager (etc.) keeps their normal rights.
_SYSTEM_ROLES = {"All", "Guest", "Desk User", "Price Check", "pricecheck"}

# Desk User's inherited Item select/report must not expose masters or stock
# to unauthorized floor staff on the price-check terminal.
_BLOCKED_DOCTYPES = {
	"Bin",
	"Batch",
	"Delivery Note",
	"Item",
	"Item Price",
	"Item Barcode",
	"POS Invoice",
	"Purchase Invoice",
	"Purchase Order",
	"Purchase Receipt",
	"Sales Invoice",
	"Sales Order",
	"Serial No",
	"Stock Entry",
	"Stock Ledger Entry",
	"Stock Reconciliation",
	"Warehouse",
}


def _is_price_check_kiosk(user: str | None = None) -> bool:
	roles = set(frappe.get_roles(user))
	if "Price Check" not in roles and "pricecheck" not in roles:
		return False
	return not (roles - _SYSTEM_ROLES)


def get_permission_query_conditions(user: str | None = None) -> str:
	if not user:
		user = frappe.session.user
	if user in ("Administrator", "Guest"):
		return ""
	if _is_price_check_kiosk(user):
		return "1=0"
	return ""


def has_permission(doc, ptype: str | None = None, user: str | None = None, **_kwargs) -> bool:
	"""Controllers may only deny. Return True to leave the decision to roles."""
	if not user:
		user = frappe.session.user
	if user == "Administrator":
		return True
	if not _is_price_check_kiosk(user):
		return True
	return False
