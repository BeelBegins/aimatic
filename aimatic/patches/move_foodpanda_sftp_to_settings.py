# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

"""Move Foodpanda SFTP credentials off Branch onto Foodpanda Settings.

Per-branch chain ID and SFTP enable/schedule move onto Foodpanda Outlet.
Branch custom SFTP fields are hidden (not deleted) so existing values remain
recoverable. Does not log or print passwords.
"""

import frappe
from frappe.utils import cint

_BRANCH_SFTP_FIELDS = (
	"Branch-custom_fp_sftp_section",
	"Branch-custom_fp_sftp_enabled",
	"Branch-custom_fp_sftp_schedule_time",
	"Branch-custom_fp_sftp_host",
	"Branch-custom_fp_sftp_port",
	"Branch-custom_fp_sftp_column_break",
	"Branch-custom_fp_sftp_username",
	"Branch-custom_fp_sftp_password",
	"Branch-custom_fp_sftp_remote_path",
	"Branch-custom_fp_sftp_status_section",
	"Branch-custom_fp_sftp_last_upload",
	"Branch-custom_fp_sftp_last_error",
)


def execute():
	_copy_default_chain_id_to_outlets()
	_copy_branch_sftp_to_settings_and_outlets()
	_hide_branch_sftp_fields()


def _copy_default_chain_id_to_outlets():
	if not frappe.db.exists("DocType", "Foodpanda Outlet"):
		return
	if not frappe.db.has_column("Foodpanda Outlet", "chain_id"):
		return
	default_chain = (frappe.db.get_single_value("Foodpanda Settings", "chain_id") or "").strip()
	if not default_chain:
		return
	frappe.db.sql(
		"""
		update `tabFoodpanda Outlet`
		set chain_id = %s
		where ifnull(chain_id, '') = ''
		""",
		default_chain,
	)


def _copy_branch_sftp_to_settings_and_outlets():
	if not frappe.db.has_column("Branch", "custom_fp_sftp_host"):
		return

	settings = frappe.get_single("Foodpanda Settings")
	copied_connection = False
	branches = frappe.get_all("Branch", pluck="name", order_by="name asc")
	for branch in branches:
		if not frappe.db.exists("Branch", branch):
			continue
		branch_doc = frappe.get_doc("Branch", branch)
		host = (branch_doc.get("custom_fp_sftp_host") or "").strip()
		username = (branch_doc.get("custom_fp_sftp_username") or "").strip()
		remote_path = (branch_doc.get("custom_fp_sftp_remote_path") or "").strip()
		port = cint(branch_doc.get("custom_fp_sftp_port")) or 22
		password = None
		try:
			password = branch_doc.get_password("custom_fp_sftp_password", raise_exception=False)
		except Exception:
			password = None

		if not copied_connection and (host or username or password):
			if not (settings.sftp_host or "").strip() and host:
				settings.sftp_host = host
			if not cint(settings.sftp_port) and port:
				settings.sftp_port = port
			if not (settings.sftp_username or "").strip() and username:
				settings.sftp_username = username
			if not (settings.sftp_remote_path or "").strip() and remote_path:
				settings.sftp_remote_path = remote_path
			if password and not settings.get_password("sftp_password", raise_exception=False):
				settings.sftp_password = password
			if not (settings.sftp_filename_prefix or "").strip():
				settings.sftp_filename_prefix = "catalog"
			copied_connection = True
			settings.save(ignore_permissions=True)

		outlet_name = frappe.db.get_value("Foodpanda Outlet", {"branch": branch}, "name")
		if not outlet_name or not frappe.db.has_column("Foodpanda Outlet", "sftp_enabled"):
			continue
		values = {}
		if cint(branch_doc.get("custom_fp_sftp_enabled")):
			values["sftp_enabled"] = 1
		schedule = branch_doc.get("custom_fp_sftp_schedule_time")
		if schedule:
			values["sftp_schedule_time"] = schedule
		last_upload = branch_doc.get("custom_fp_sftp_last_upload")
		if last_upload:
			values["sftp_last_upload"] = last_upload
		last_error = branch_doc.get("custom_fp_sftp_last_error")
		if last_error:
			values["sftp_last_error"] = last_error
		if values:
			frappe.db.set_value("Foodpanda Outlet", outlet_name, values, update_modified=False)


def _hide_branch_sftp_fields():
	for name in _BRANCH_SFTP_FIELDS:
		if frappe.db.exists("Custom Field", name):
			frappe.db.set_value("Custom Field", name, "hidden", 1, update_modified=False)
