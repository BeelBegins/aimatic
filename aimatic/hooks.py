import json
from pathlib import Path

app_name = "aimatic"
app_title = "Aimatic"
app_publisher = "Aimatic"
app_description = "Retail"
app_email = "sthassan41@gmail.com"
app_license = "mit"
app_logo_url = "/assets/aimatic/images/aimatic-logo.svg"
app_home = "/desk/aimatic"


doc_events = {
	"POS Invoice": {
		"validate": "aimatic.fbr_pos.events.validate_pos_invoice",
		"before_submit": "aimatic.fbr_pos.events.before_submit_pos_invoice",
		"on_submit": [
			# Runs after POS Invoice.clear_unallocated_mode_of_payments deletes
			# amount=0 payment rows — restores the Food Panda Credit marker.
			"aimatic.offline_pos.events.restore_food_panda_credit_payment_marker",
			"aimatic.loyalty.events.on_submit_correct_loyalty_points",
			"aimatic.gift_voucher.events.on_submit_issue_gift_voucher",
		],
		"on_cancel": [
			"aimatic.fbr_pos.events.on_cancel_pos_invoice",
			"aimatic.gift_voucher.events.on_cancel_gift_voucher",
		],
	},
	"Customer": {
		"validate": "aimatic.offline_pos.customer_validation.validate_customer",
	},
	# Denormalize the Item's ordered barcode child rows into a read-only Item
	# Price field so Frappe's standard Export Data feature can include them.
	"Item": {
		"autoname": "aimatic.item_naming.events.autoname_item_code_from_series",
		"validate": [
			"aimatic.item_naming.events.block_duplicate_barcode_on_create",
			"aimatic.item_naming.events.ensure_item_barcode_row",
		],
		"on_update": "aimatic.item_pricing.barcodes.sync_item_barcodes_to_prices",
	},
	"Item Price": {
		"before_validate": "aimatic.item_pricing.barcodes.set_item_price_barcodes",
	},
	# Auto-assigns a new Account's account_number (when left blank) to the
	# next free number in the numeric block implied by its parent account -
	# removes the error-prone manual "what's the next number in this series"
	# judgment call from whoever is adding a new ledger account.
	"Account": {
		"validate": "aimatic.coa_numbering.events.auto_assign_account_number",
	},
	# Branch Management: derives cost_center / set_warehouse / rejected_warehouse
	# from the document's Branch so normal Sales/Purchase users never pick these
	# (and can't pick them wrong). POS Invoice is deliberately excluded - see
	# aimatic.branch_management.events.apply_branch_defaults docstring.
	"Sales Order": {
		"before_validate": "aimatic.branch_management.events.apply_branch_defaults",
		"before_submit": "aimatic.mobile_sales.events.before_submit_sales_order",
	},
	"Sales Invoice": {
		"before_validate": "aimatic.branch_management.events.apply_branch_defaults",
	},
	"Delivery Note": {
		"before_validate": "aimatic.branch_management.events.apply_branch_defaults",
	},
	"Purchase Order": {
		"before_validate": [
			"aimatic.branch_management.events.apply_branch_defaults",
			"aimatic.purchase_printing.populate_old_purchase_snapshot",
			"aimatic.purchase_history_autofill.events.autofill_purchase_order_item_fields",
		],
		"validate": "aimatic.purchase_principal.validate_purchase_principal",
	},
	"Purchase Invoice": {
		"before_validate": [
			"aimatic.branch_management.events.apply_branch_defaults",
			"aimatic.purchase_printing.populate_old_purchase_snapshot",
			"aimatic.purchase_history_autofill.events.autofill_purchase_invoice_item_fields",
			"aimatic.purchase_supplier_invoice.prefill_purchase_invoice_supplier_invoice",
			"aimatic.purchase_principal.prefill_purchase_invoice_principal",
			# PI returns must update stock (Bin); normal PIs use PR for stock.
			"aimatic.purchase_return_stock.ensure_purchase_invoice_return_updates_stock",
		],
		"validate": "aimatic.purchase_principal.validate_purchase_principal",
		"on_submit": "aimatic.item_pricing.events.update_latest_price_incl_taxes",
	},
	"Purchase Receipt": {
		"before_validate": [
			"aimatic.branch_management.events.apply_branch_defaults",
			"aimatic.purchase_printing.populate_old_purchase_snapshot",
			"aimatic.purchase_history_autofill.events.autofill_purchase_receipt_item_fields",
			"aimatic.purchase_supplier_invoice.prefill_purchase_receipt_supplier_invoice",
			"aimatic.purchase_principal.prefill_purchase_receipt_principal",
		],
		"validate": "aimatic.purchase_principal.validate_purchase_principal",
		# Desk amend ignores Custom Field no_copy; force Pending so the
		# submit dialog / retry buttons can apply prices for the amendment.
		"before_insert": "aimatic.shelf_pricing.events.reset_price_update_status_on_amend",
		# After validate: PR commercial Server Script ("Before Save") sets
		# custom_price_after_taxes during validate, so GM % must run here.
		"before_save": "aimatic.shelf_pricing.events.set_shelf_gm_percent",
		# shelf_pricing: Shelf Price must not undercut cost before the
		# receipt is allowed to submit. The actual branch/Foodpanda price
		# propagation is client-script-driven (popups + retry buttons)
		# calling aimatic.shelf_pricing.api, not a hook - see the
		# shelf_pricing module docstring.
		"before_submit": "aimatic.shelf_pricing.events.validate_shelf_price_before_submit",
		"on_submit": "aimatic.item_pricing.events.update_latest_price_incl_taxes",
		"on_cancel": "aimatic.shelf_pricing.events.restore_prices_on_cancel",
	},
	# Stock Entry: only branch/cost_center get defaulted here (it has no
	# set_warehouse/rejected_warehouse fields, so those parts of
	# apply_branch_defaults are harmless no-ops for this doctype). Needed so
	# its branch field - a mandatory Accounting Dimension for P&L accounts -
	# is populated before GL entries are made (e.g. the Stock Adjustment
	# account on a Material Issue/Receipt/Repack entry).
	"Stock Entry": {
		"before_validate": "aimatic.branch_management.events.apply_branch_defaults",
	},
	# foodpanda_integration: pushes an item's Foodpanda availability whenever
	# its Bin quantity changes at a branch with catalog sync enabled, so an
	# item that sells out in ERPNext stops being orderable on Foodpanda
	# before it can be oversold. Debounced in events.on_bin_update.
	"Bin": {
		"on_update": "aimatic.foodpanda_integration.events.on_bin_update",
	},
	"Branch": {
		"validate": "aimatic.branch_management.events.validate_branch_company_consistency",
		"after_insert": "aimatic.branch_management.events.initialize_branch_selling_price_list",
	},
	# POS Invoice is excluded from apply_branch_defaults (built server-side from
	# POS Profile already) - this is the one remaining check that a POS Profile
	# isn't pointed at a group Cost Center, which ERPNext would otherwise only
	# reject much later, at shift-close GL-entry time.
	"POS Profile": {
		"validate": [
			"aimatic.branch_management.events.apply_pos_profile_branch_price_list",
			"aimatic.branch_management.events.validate_pos_profile_cost_center",
			"aimatic.gift_voucher.events.validate_pos_profile_no_manual_gift_voucher_payment",
		],
	},
	# Tax ID/NTN is the entry field. Supplier save no longer validates or
	# reformats tax_id; tax_ntn remains a read-only mirror of the entered value.
	# FBR NTN verification is an explicit button action on the Supplier form,
	# backed by aimatic.api.fbr_taxpayer_verification.verify_supplier_ntn.
	"Supplier": {
		"validate": "aimatic.supplier_management.events.validate_supplier_ntn",
	},
	# Mirrors a user's default Branch (assigned via User Permission) onto
	# User.custom_branch so it's visible as a column on the User list view.
	"User Permission": {
		"after_insert": "aimatic.branch_management.events.sync_branch_to_user",
		"after_delete": "aimatic.branch_management.events.sync_branch_to_user",
	},
	# Record enable/disable changes. The Android auth hook below enforces the
	# new state on the device's next token or API request and revokes the token.
	"POS Device": {
		"on_update": "aimatic.aimatic.offline_pos.device_events.on_pos_device_update",
	},
}

auth_hooks = [
	"aimatic.aimatic.offline_pos.device_auth.validate_android_pos_device_auth",
	# Foodpanda WebhookKeyAuth often arrives as "Bearer <secret>". Frappe core
	# treats two-part Authorization as OAuth/API-key and 401s Guest before the
	# allow_guest catalog/order methods run — accept matching webhook secret first.
	"aimatic.foodpanda_integration.webhooks.validate_foodpanda_webhook_auth",
]

# Redirects frappe.integrations.oauth2.get_token to our own implementation so
# refresh-token rotation/replay detection (aimatic.aimatic.oauth.validator)
# applies without modifying frappe core. See aimatic/aimatic/oauth/endpoints.py.
override_whitelisted_methods = {
	"frappe.integrations.oauth2.get_token": "aimatic.aimatic.oauth.endpoints.get_token",
	# Prefill Supplier Invoice No on Create (PO/PR → child); editable after.
	"erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt": (
		"aimatic.purchase_supplier_invoice.make_purchase_receipt"
	),
	"erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice": (
		"aimatic.purchase_supplier_invoice.make_purchase_invoice_from_po"
	),
	"erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice": (
		"aimatic.purchase_supplier_invoice.make_purchase_invoice_from_pr"
	),
}

# Label Printing module: adds a "Create Barcode Labels" button to Purchase
# Receipt, Purchase Order, Delivery Note, Stock Entry, and Sales Invoice via
# client script only. None of these doctypes are ever modified server-side.
doctype_js = {
	"Purchase Receipt": [
		"public/js/purchase_receipt_label_printing.js",
		"public/js/purchase_items_excel_grid.js",
		# Live preview only - see purchase_history_autofill/events.py for
		# the actual server-side guarantee this mirrors. Shared with
		# Purchase Order / Purchase Invoice below.
		"public/js/purchase_history_autofill.js",
		# Live preview only - prefills custom_fp_price from the branch's
		# current Foodpanda Price List. Shared with Purchase Order/Invoice.
		"public/js/foodpanda_price_prefill.js",
		# Live preview only - prefills custom_shelf_price (Sale Price) from
		# the branch's current Selling Price List rate, for reference/edit.
		"public/js/current_sale_price_preview.js",
		# Live read-only GM % between Sale Price and Price After Taxes.
		"public/js/shelf_gm_percent.js",
		# Resolve item_code for barcodes pasted via Items grid Upload.
		"public/js/purchase_barcode_import.js",
		"public/js/purchase_principal.js",
	],
	"Purchase Order": [
		"public/js/purchase_items_excel_grid.js",
		"public/js/purchase_history_autofill.js",
		"public/js/foodpanda_price_prefill.js",
		"public/js/label_printing_source_buttons.js",
		# Resolve item_code for barcodes pasted via Items grid Upload.
		"public/js/purchase_barcode_import.js",
		"public/js/purchase_principal.js",
	],
	"Purchase Invoice": [
		"public/js/purchase_items_excel_grid.js",
		"public/js/purchase_history_autofill.js",
		"public/js/foodpanda_price_prefill.js",
		"public/js/purchase_principal.js",
		"public/js/purchase_return_stock.js",
	],
	"Delivery Note": "public/js/label_printing_source_buttons.js",
	"Stock Entry": "public/js/label_printing_source_buttons.js",
	"Sales Invoice": "public/js/label_printing_source_buttons.js",
	"Supplier": "public/js/supplier_vendor_performance.js",
	"Price List": "public/js/price_list_barcode_search.js",
	"POS Profile": "public/js/pos_profile_device_enrollment.js",
	"Foodpanda Outlet": "public/js/foodpanda_outlet_sync.js",
	"Foodpanda Settings": "public/js/foodpanda_settings.js",
	"Foodpanda Product": "public/js/foodpanda_product_sync.js",
	"Branch": "public/js/branch_foodpanda_price_import.js",
}

# Desk list helpers: resolve every barcode through the Item Barcode child table,
# then filter the parent Item or its Item Price rows without denormalizing data.
doctype_list_js = {
	"Item": "public/js/barcode_list_search.js",
	"Item Price": "public/js/barcode_list_search.js",
}

jinja = {
	"methods": [
		"aimatic.label_printing.barcode_render.get_barcode_data_uri",
		"aimatic.label_printing.utils.expand_print_rows",
		"aimatic.label_printing.utils.get_template_context",
		"aimatic.label_printing.utils.format_price",
		"aimatic.purchase_printing.get_purchase_print_item_context",
		"aimatic.pos_printing.get_pos_receipt_context",
		"aimatic.print_layout_loader.render_print_layout",
	],
}

# Creates Label Printing defaults, Restaurant roles, and the POS roles plus
# Item master-data grants on a fresh `bench install-app aimatic`. Frappe marks
# patches completed instead of executing them during first installation, so
# the equivalent patches handle upgrades while this hook handles new sites.
# Print Format/Report packaging does NOT need an after_install entry - see the
# print-format-packaging skill for how those ship via Frappe's native
# module-doc sync instead.
after_install = "aimatic.setup.after_install"
after_migrate = "aimatic.tax_formula_setup.after_migrate"

_fixture_exclusions = json.loads(
	(Path(__file__).with_name("fixture_exclusions.json")).read_text(encoding="utf-8")
)

# Every Custom Field/Property Setter/Client Script/Server Script is treated as
# an aimatic fixture EXCEPT the specific foreign/system records named in
# fixture_exclusions.json - Frappe's own Accounting Dimension auto-injected
# `-branch` fields, HRMS's own fields, Pakistan NIC/NTN/STRN localization
# fields, CRM integration fields, and core system fields (`impersonate`).
# This is deliberately an exclude-list, not an allowlist: a genuinely new
# aimatic customization is included automatically the next time fixtures are
# exported, with no manual step - only *newly appearing foreign/system*
# records ever need adding here, which is rare and self-evident (it'll show
# up unexpectedly in an `export-fixtures` diff).
# Run `bench --site <site> export-fixtures --app aimatic` after changing
# Custom Fields, Customize Form settings, Client Scripts, or Server Scripts.
fixtures = [
	{"doctype": "Custom Field", "filters": [["name", "not in", _fixture_exclusions["Custom Field"]]]},
	{"doctype": "Property Setter", "filters": [["name", "not in", _fixture_exclusions["Property Setter"]]]},
	{"doctype": "Client Script", "filters": [["name", "not in", _fixture_exclusions["Client Script"]]]},
	{"doctype": "Server Script", "filters": [["name", "not in", _fixture_exclusions["Server Script"]]]},
	# GST/Advance Tax/FED formula definitions used by the Purchase Receipt/
	# Purchase Order tax-calc client scripts - configuration, not transactional
	# data, so it ships with the app rather than being recreated by hand on
	# every new site. Account links are company-specific even though Tax
	# Formula has no company field, so a fixture value can become invalid on
	# another site. The after_migrate hook repairs only missing/dangling GST
	# links on unambiguous single-company sites by selecting that company's
	# enabled ``GST - <abbr>`` tax ledger. Existing valid mappings and all
	# multi-company sites are left untouched; other account types are never
	# guessed. A standalone `sync_fixtures` call (bypassing `bench migrate`)
	# reintroduces the dangling placeholder without running this repair -
	# the daily scheduler entry below is the safety net for that case.
	{"doctype": "Tax Formula"},
	# Reference GST categories for FBR e-invoicing - configuration, no secrets.
	{"doctype": "FBR Tax Category"},
	# Custom DocPerm: ERPNext's own default DocPerm for POS Opening Entry/POS
	# Closing Entry only grants System Manager/Sales Manager/Administrator -
	# POS User and POS Supervisor have zero permission on either doctype out
	# of the box, even though offline_pos/api.py's own role model
	# (_ALLOWED_CASHIER_ROLES/_ALLOWED_CLOSE_SHIFT_ROLES) clearly intends POS
	# User to start shifts and POS Supervisor to close them. Discovered
	# 2026-07-15 testing a real cashier account: frappe.has_permission
	# returned False on all four create/submit checks despite the correct
	# roles being assigned. Custom DocPerm rows here grant exactly that,
	# without touching erpnext's own doctype JSON.
	#
	# Second batch (2026-07-19): the Electron POS terminal's own startup/settings
	# flow (Posapplication's core/pos-config.ts, core/catalog-sync.ts,
	# core/sale-refund.ts) reads several master-data doctypes directly over
	# Frappe's generic /api/resource REST endpoint - not through offline_pos's
	# whitelisted, already-permission-checked methods - so those calls are
	# subject to each doctype's own standard DocPerm. None of POS Profile,
	# Company, Sales Taxes and Charges Template, Mode of Payment, Coupon Code,
	# Customer Group, Territory, Item, Item Price, Bin, Branch, or Print Format
	# grant POS User/POS Supervisor anything out of the box (core ERPNext
	# expects POS-role users to go through the native POS Awesome page's
	# server-side RPCs instead, which bypass doctype permissions internally -
	# this Electron client doesn't). Confirmed live on szl 2026-07-19: a
	# cashier with only POS User+POS Supervisor could authenticate (API
	# key/secret) but "Test Login" then failed to load POS Profiles/terminal
	# ID/assigned warehouse, only working once given every role in the system.
	# Customer additionally needs create (walk-in customer registration from
	# the terminal); everything else here is read-only.
	# Filtered on role too, not just parent doctype - Company/Customer/Branch/Mode of
	# Payment etc. already carry plenty of pre-existing Custom DocPerm rows for other
	# roles (Accounts Manager, HR Manager, Sales User, ...) that have nothing to do with
	# this POS-terminal fix and were never fixture-tracked; a parent-only filter would
	# sweep those in too and risk duplicate rows on sites where they already exist under
	# a different auto-generated name once `migrate` syncs this fixture.
	{
		"doctype": "Custom DocPerm",
		"filters": [
			[
				"parent",
				"in",
				[
					"POS Opening Entry",
					"POS Closing Entry",
					"POS Profile",
					"Company",
					"Sales Taxes and Charges Template",
					"Mode of Payment",
					"Coupon Code",
					"Customer",
					"Customer Group",
					"Territory",
					"Item Price",
					"Bin",
					"Branch",
					"Print Format",
				],
			],
			["role", "in", ["POS User", "POS Supervisor", "Buying Price Control", "Price Check"]],
		],
	},
	# Item is deliberately NOT fixture-tracked here, unlike the other doctypes above -
	# Custom DocPerm's own autoname is "hash", and fixture-sync's import_doc() does not
	# reliably converge to a stable set of names for it across repeated bench migrate
	# runs in practice (confirmed: an earlier fixture-based attempt at this exact fix
	# kept re-creating a fresh duplicate 11-row batch on every migrate, unbounded).
	# aimatic.patches.repair_item_custom_docperms is used instead - a content-based
	# (not name-based) reconciliation that rebuilds Item's complete Custom DocPerm set
	# (standard DocPerm rows, since any Custom DocPerm on a doctype replaces ALL of
	# its standard DocPerm rows, plus the two POS read+export grants) directly against
	# what's actually in the database. It runs through a patch for upgrades and the
	# app's after_install/test bootstrap for new sites and CI, sidestepping fixture
	# hash-name instability in every path.
	# POS Invoice itself (2026-07-20): the same class of gap as above, but discovered
	# one step further down the same flow - a cashier with POS User could log in, open
	# a shift, and preview a cart, but submit_online_sale's own
	# `frappe.has_permission("POS Invoice", "create")` check threw "Not permitted to
	# create POS Invoice", since ERPNext's stock POS Invoice DocPerm only grants
	# Accounts Manager/Accounts User. Confirmed live on siezal against a real blocked
	# cashier (cashier3@aimatic.tech, roles POS User only).
	#
	# Unlike the doctypes above, POS Invoice had ZERO pre-existing Custom DocPerm rows,
	# so this is filtered on parent alone (no role restriction) rather than an
	# allowlisted role subset: Frappe's permission engine treats "any Custom DocPerm
	# row exists for this parent" as license to ignore that doctype's standard DocPerm
	# entirely (frappe.permissions.get_valid_perms/get_doctypes_with_custom_docperms) -
	# so fixture-syncing only the new POS User/POS Supervisor rows onto a site that
	# doesn't already have this override would silently strip Accounts Manager/Accounts
	# User/All's own POS Invoice permissions on that site. Capturing every role/permlevel
	# row here (the ones Frappe itself copied forward from stock DocPerm the moment the
	# first custom row was added, plus the new POS User/POS Supervisor grants) keeps
	# this fixture a complete, safe drop-in for any site, present or future.
	# Separate fixture file (via `prefix`) rather than a second block on the same
	# "Custom DocPerm" doctype: export_fixtures derives the output filename purely
	# from the doctype name (frappe.scrub), so two blocks for the same doctype
	# write to the same file and the second silently clobbers the first - hit this
	# empirically when the initial version of this filter (no prefix) reduced
	# custom_docperm.json from ~30 rows down to just these 6 on export.
	{
		"doctype": "Custom DocPerm",
		"filters": [["parent", "=", "POS Invoice"]],
		"prefix": "pos_invoice",
	},
	# NOTE: FBR Integration Settings is deliberately NOT a fixture - it holds
	# a security_token (Password) plus real sandbox/production URLs per
	# company+branch. Only its schema is git-tracked (doctype file below);
	# each site must have its own credentials configured directly.
]

# Apps
# ------------------

# required_apps = []

# Register Aimatic in Frappe's app switcher. The public Aimatic workspace and
# its permission-filtered Workspace Sidebar remain the navigation authority.
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": app_logo_url,
		"title": app_title,
		"route": app_home,
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/aimatic/css/aimatic.css"
# app_include_js = "/assets/aimatic/js/aimatic.js"

# include js, css files in header of web template
# web_include_css = "/assets/aimatic/css/aimatic.css"
# web_include_js = "/assets/aimatic/js/aimatic.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "aimatic/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "aimatic/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "aimatic.utils.jinja_methods",
# 	"filters": "aimatic.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "aimatic.install.before_install"
# after_install = "aimatic.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "aimatic.uninstall.before_uninstall"
# after_uninstall = "aimatic.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "aimatic.utils.before_app_install"
# after_app_install = "aimatic.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "aimatic.utils.before_app_uninstall"
# after_app_uninstall = "aimatic.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "aimatic.notifications.get_notification_config"

# Permissions
# -----------
# Price Check kiosk users inherit Desk User's Item select/report via Frappe's
# automatic Desk User role. Deny stock/master doctypes for pure Price Check
# accounts so floor terminals cannot browse Item/Bin or stock value reports.
_price_check_perm = "aimatic.price_check.permissions.get_permission_query_conditions"
_price_check_has = "aimatic.price_check.permissions.has_permission"
permission_query_conditions = {
	dt: _price_check_perm
	for dt in (
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
	)
}
has_permission = {dt: _price_check_has for dt in permission_query_conditions}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": [
		"aimatic.ai.tasks.purge_old_ai_messages",
		"aimatic.ai.tasks.run_scheduled_questions",
		"aimatic.ai.tasks.check_alert_rules",
		# Safety net: a standalone fixture sync (bypassing `bench migrate`)
		# resets Tax Formula.gst_account/advance_tax_account to the shared
		# fixture's values (a placeholder for gst_account, blank for
		# advance_tax_account) and skips the after_migrate repair below.
		# This self-heals daily.
		"aimatic.tax_formula_setup.repair_dangling_tax_formula_accounts",
	],
	# Branch Foodpanda SFTP: each Branch sets its own schedule time; this cron
	# only checks which enabled branches are due (about every 15 minutes).
	"cron": {
		"*/15 * * * *": [
			"aimatic.price_export.foodpanda_sftp.run_scheduled_foodpanda_sftp_uploads",
		],
	},
}

# Testing
# -------

before_tests = "aimatic.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "aimatic.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "aimatic.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "aimatic.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["aimatic.utils.before_request"]
# after_request = ["aimatic.utils.after_request"]

# Job Events
# ----------
# before_job = ["aimatic.utils.before_job"]
# after_job = ["aimatic.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"aimatic.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# Stock Ledger barcodes column (Desk report URL unchanged). Applied on hooks
# import and again on first request so gunicorn --preload / late workers pick it up.
from aimatic.stock_reports.stock_ledger_barcodes import patch_stock_ledger_report

patch_stock_ledger_report()

before_request = [
	"aimatic.stock_reports.stock_ledger_barcodes.patch_stock_ledger_report",
]
