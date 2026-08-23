import frappe

LEGACY_ROLE = "pricecheck"
KIOSK_ROLE = "Price Check"
# Only these accounts should keep a price-check home. Everyone else who still
# carries the legacy role (e.g. Administrator) must not land on the kiosk.
KIOSK_USERS = ("price@aimatic.tech",)


def execute():
	"""Stop non-kiosk users from being redirected to Price Check on login.

	Role.home_page is evaluated for every role on the user; the first hit wins.
	The legacy ``pricecheck`` role was left with
	``/desk/price-check-console``, and Administrator still had that role, so
	Admin login jumped straight into the kiosk.
	"""
	if frappe.db.exists("Role", LEGACY_ROLE):
		frappe.db.set_value("Role", LEGACY_ROLE, "home_page", None)

	# Never put a Role-level home on Price Check: System Managers who also hold
	# that role (ops accounts) would be redirected too. Kiosk users use
	# User.default_workspace instead.
	if frappe.db.exists("Role", KIOSK_ROLE):
		frappe.db.set_value("Role", KIOSK_ROLE, "home_page", None)

	for row in frappe.get_all(
		"Has Role",
		filters={"role": LEGACY_ROLE, "parenttype": "User"},
		fields=["name", "parent"],
	):
		frappe.db.delete("Has Role", {"name": row.name})
		# Dedicated kiosk keeps the new role + workspace; others just lose the
		# legacy redirect.
		if row.parent in KIOSK_USERS and not frappe.db.exists(
			"Has Role", {"parent": row.parent, "role": KIOSK_ROLE}
		):
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": row.parent,
					"parenttype": "User",
					"parentfield": "roles",
					"role": KIOSK_ROLE,
				}
			).insert(ignore_permissions=True)

	frappe.clear_cache()
