"""Scoped, repeatable installation. No transaction documents or credentials seeded."""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

DOCTYPES = (
	"Retail Commitment Item",
	"Retail Commitment Receipt",
	"Retail Reference Item",
	"Retail Operations Settings",
	"Retail Operations Run",
	"Retail Operations Issue",
	"Retail Discord Delivery",
	"Retail Purchase Commitment",
	"Retail Product Reference",
	"Retail Replenishment Rule",
	"Retail POS Void Event",
)


def install():
	for name in DOCTYPES:
		frappe.reload_doc("aimatic", "doctype", name.lower().replace(" ", "_"), force=True)
	fields = [
		dict(
			fieldname="custom_discord_section",
			label="Discord Channels",
			fieldtype="Section Break",
			insert_after="finished_goods_warehouse",
			permlevel=1,
		)
	]
	previous = "custom_discord_section"
	for channel in ("Inventory", "Accounts", "Sales", "Purchase"):
		field = "custom_discord_" + channel.lower()
		fields.append(
			dict(
				fieldname=field,
				label=channel + " Webhook",
				fieldtype="Password",
				insert_after=previous,
				permlevel=1,
			)
		)
		previous = field
	for field, label in (
		("custom_operations_enabled", "Enable Operations Reports"),
		("custom_operations_auto_requests", "Enable Reviewed Automatic Material Requests"),
	):
		fields.append(
			dict(
				fieldname=field,
				label=label,
				fieldtype="Check",
				default="0",
				insert_after=previous,
				permlevel=1,
			)
		)
		previous = field
	create_custom_fields(
		{
			"Branch": fields,
			"Material Request": [
				dict(
					fieldname="custom_operations_vendor",
					label="Operations Vendor",
					fieldtype="Link",
					options="Supplier",
					read_only=1,
					insert_after="company",
				),
				dict(
					fieldname="custom_operations_run",
					label="Operations Run Reference",
					fieldtype="Data",
					read_only=1,
					insert_after="custom_operations_vendor",
				),
			],
		},
		update=True,
	)
	# Custom DocPerm overrides the standard table: preserve its grants first.
	for standard in frappe.get_doc("DocType", "Branch").permissions:
		if not frappe.db.exists(
			"Custom DocPerm",
			{"parent": "Branch", "role": standard.role, "permlevel": standard.permlevel or 0},
		):
			values = {
				key: standard.get(key)
				for key in (
					"role",
					"permlevel",
					"read",
					"write",
					"create",
					"delete",
					"select",
					"report",
					"export",
					"print",
					"email",
					"share",
					"submit",
					"cancel",
					"amend",
					"if_owner",
				)
			}
			values.update(
				doctype="Custom DocPerm", parent="Branch", parenttype="DocType", parentfield="permissions"
			)
			frappe.get_doc(values).insert(ignore_permissions=True)
	for role in ("Stock Manager", "Purchase Manager", "Sales Manager", "Accounts Manager"):
		if not frappe.db.exists("Custom DocPerm", {"parent": "Branch", "role": role, "permlevel": 0}):
			frappe.get_doc(
				dict(
					doctype="Custom DocPerm",
					parent="Branch",
					parenttype="DocType",
					parentfield="permissions",
					role=role,
					permlevel=0,
					read=1,
					select=1,
				)
			).insert(ignore_permissions=True)
	if not frappe.get_meta("Material Request").has_field("branch"):
		create_custom_fields(
			{
				"Material Request": [
					dict(
						fieldname="branch",
						label="Branch",
						fieldtype="Link",
						options="Branch",
						insert_after="company",
					)
				]
			}
		)
	frappe.db.add_unique(
		"Retail Replenishment Rule", ["branch", "item_code"], constraint_name="retail_branch_item_unique"
	)
	if not frappe.db.exists("Custom DocPerm", {"parent": "Branch", "role": "System Manager", "permlevel": 1}):
		frappe.get_doc(
			dict(
				doctype="Custom DocPerm",
				parent="Branch",
				parenttype="DocType",
				parentfield="permissions",
				role="System Manager",
				permlevel=1,
				read=1,
				write=1,
			)
		).insert(ignore_permissions=True)
	for field, value in (
		("history_days", 90),
		("max_discord_pages", 8),
		("fallback_cover_days", 7),
		("max_margin_rows", 12000),
		("refund_alert_amount", 10000),
		("counter_min_receipts", 20),
		("inventory_time", "09:00:00"),
		("accounts_time", "09:30:00"),
		("sales_time", "09:15:00"),
		("purchase_time", "08:30:00"),
	):
		if frappe.db.get_single_value("Retail Operations Settings", field) is None:
			frappe.db.set_single_value("Retail Operations Settings", field, value)
	frappe.reload_doc("aimatic", "page", "retail_operations_console", force=True)
	frappe.reload_doc("aimatic", "workspace", "retail_operations", force=True)
	method = "aimatic.retail_operations.tasks.tick"
	if not frappe.db.exists("Scheduled Job Type", {"method": method}):
		frappe.get_doc(
			dict(doctype="Scheduled Job Type", method=method, frequency="Cron", cron_format="*/15 * * * *")
		).insert(ignore_permissions=True)
	method = "aimatic.retail_operations.tasks.drain_deliveries"
	if not frappe.db.exists("Scheduled Job Type", {"method": method}):
		frappe.get_doc(
			dict(doctype="Scheduled Job Type", method=method, frequency="Cron", cron_format="* * * * *")
		).insert(ignore_permissions=True)
	frappe.clear_cache(doctype="Branch")
	return {
		"installed": list(DOCTYPES),
		"enabled": bool(frappe.db.get_single_value("Retail Operations Settings", "enabled")),
	}
