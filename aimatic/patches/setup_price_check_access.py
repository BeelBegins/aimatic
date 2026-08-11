import frappe

ROLE = "Price Check"
LEGACY_ROLE = "pricecheck"
MODULE_PROFILE = "Price Check"
WORKSPACE = "Price Check"
PAGE = "price-check-console"

# Hide every operational module from Price Check kiosk users. Aimatic stays
# visible so the dedicated Price Check workspace (and its page) can load;
# Stock/Selling/Buying/Accounts/etc. are what exposed the 3.53cr position.
BLOCKED_MODULES = (
	"Accounts",
	"Assets",
	"Buying",
	"CRM",
	"EDI",
	"ERPNext Integrations",
	"HR",
	"Label Printing",
	"Maintenance",
	"Manufacturing",
	"Payroll",
	"Portal",
	"Projects",
	"Quality Management",
	"Regional",
	"Selling",
	"Setup",
	"Stock",
	"Subcontracting",
	"Support",
	"Telephony",
	"Website",
	"Workflow",
)


def _ensure_role():
	if frappe.db.exists("Role", ROLE):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": ROLE,
			"desk_access": 1,
			"is_custom": 1,
		}
	).insert(ignore_permissions=True)


def _ensure_module_profile():
	if frappe.db.exists("Module Profile", MODULE_PROFILE):
		doc = frappe.get_doc("Module Profile", MODULE_PROFILE)
	else:
		doc = frappe.new_doc("Module Profile")
		doc.module_profile_name = MODULE_PROFILE

	existing = {row.module for row in doc.get("block_modules") or []}
	for module in BLOCKED_MODULES:
		if module not in existing and frappe.db.exists("Module Def", module):
			doc.append("block_modules", {"module": module})
	doc.save(ignore_permissions=True)


def _ensure_branch_perm():
	filters = {"parent": "Branch", "role": ROLE, "permlevel": 0}
	if frappe.db.exists("Custom DocPerm", filters):
		return
	frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": "Branch",
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": ROLE,
			"permlevel": 0,
			"read": 1,
			"select": 1,
			"write": 0,
			"create": 0,
			"delete": 0,
			"submit": 0,
			"cancel": 0,
			"amend": 0,
			"report": 0,
			"export": 0,
			"import": 0,
			"share": 0,
			"print": 0,
			"email": 0,
		}
	).insert(ignore_permissions=True)


def _restrict_page():
	if not frappe.db.exists("Page", PAGE):
		return
	page = frappe.get_doc("Page", PAGE)
	wanted = {ROLE, "System Manager"}
	have = {row.role for row in page.get("roles") or []}
	if have == wanted:
		return
	page.set("roles", [])
	for role in sorted(wanted):
		page.append("roles", {"role": role})
	page.save(ignore_permissions=True)


def _ensure_workspace():
	content = (
		'[{"id":"pcHeader","type":"header","data":{"text":'
		'"<span class=\\"h4\\"><b>Price Check</b></span>","col":12}},'
		'{"id":"pcShortcut","type":"shortcut","data":{"shortcut_name":"Price Check","col":4}}]'
	)
	if frappe.db.exists("Workspace", WORKSPACE):
		ws = frappe.get_doc("Workspace", WORKSPACE)
	else:
		ws = frappe.new_doc("Workspace")
		ws.name = WORKSPACE
		ws.label = WORKSPACE
		ws.module = "Aimatic"
		ws.public = 1
		ws.icon = "scan"
		ws.sequence_id = 5

	ws.title = WORKSPACE
	ws.content = content
	ws.parent_page = ""
	ws.is_hidden = 0
	ws.set("roles", [])
	ws.append("roles", {"role": ROLE})
	ws.set("shortcuts", [])
	ws.append(
		"shortcuts",
		{
			"label": "Price Check",
			"type": "URL",
			"url": "/app/price-check-console",
			"color": "Blue",
		},
	)
	ws.save(ignore_permissions=True)


def _migrate_users():
	"""Move dedicated price-check accounts onto the locked-down role."""
	# Legacy role name -> Price Check
	for row in frappe.get_all("Has Role", filters={"role": LEGACY_ROLE, "parenttype": "User"}, fields=["name", "parent"]):
		user = row.parent
		frappe.db.delete("Has Role", {"name": row.name})
		if not frappe.db.exists("Has Role", {"parent": user, "role": ROLE}):
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": user,
					"parenttype": "User",
					"parentfield": "roles",
					"role": ROLE,
				}
			).insert(ignore_permissions=True)

	# Dedicated kiosk account that was wrongly given POS User
	kiosk = "price@aimatic.tech"
	if frappe.db.exists("User", kiosk):
		user = frappe.get_doc("User", kiosk)
		user.set("roles", [])
		user.append("roles", {"role": ROLE})
		user.module_profile = MODULE_PROFILE
		user.default_workspace = WORKSPACE
		user.save(ignore_permissions=True)


def execute():
	_ensure_role()
	_ensure_module_profile()
	_ensure_branch_perm()
	_restrict_page()
	_ensure_workspace()
	_migrate_users()
	frappe.clear_cache()
