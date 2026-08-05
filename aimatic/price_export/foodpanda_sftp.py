# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

"""Branch-owned Foodpanda vendor-automation SFTP CSV upload.

Builds the same `barcode,sku,price,active,quantity` shape as Branch Price
Sheet's Download Foodpanda CSV, then puts the file via per-branch SFTP
credentials on Branch. This is the portal CSV path — not Partner API catalog
sync (`Foodpanda Outlet`).
"""

from __future__ import annotations

import csv
import io
from contextlib import closing

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_time, getdate, now_datetime


from aimatic.price_export.api import get_branch_price_sheet_rows, require_export_permission


TRIGGER_BRANCH = "Branch"
TRIGGER_REPORT = "Report"
TRIGGER_SCHEDULER = "Scheduler"

_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "custom_fp_sftp_password")


def _csv_filename(branch):
	return f"foodpanda-{frappe.scrub(branch)}-{frappe.utils.getdate().isoformat()}.csv"


def _primary_barcode(row):
	if row.get("barcode1"):
		return str(row["barcode1"]).strip()
	barcodes = row.get("_barcodes") or []
	if barcodes:
		return str(barcodes[0]).strip()
	return ""


def build_foodpanda_csv_rows(branch, rows=None):
	"""Return (csv_rows, skipped_count) for the vendor-upload shape.

	When ``rows`` is None, load the full branch price sheet. Otherwise treat
	``rows`` as already-filtered sheet row dicts (report upload path).
	"""
	source_rows = rows if rows is not None else get_branch_price_sheet_rows(branch)
	csv_rows = []
	skipped = 0
	for row in source_rows:
		barcode = _primary_barcode(row)
		price = flt(row.get("foodpanda_price"))
		if not barcode or price <= 0:
			skipped += 1
			continue
		quantity = max(flt(row.get("available_qty")), 0)
		csv_rows.append(
			{
				"barcode": barcode,
				"sku": "",
				"price": price,
				"active": 1 if quantity > 0 else 0,
				"quantity": quantity,
			}
		)
	return csv_rows, skipped


def build_foodpanda_csv_bytes(csv_rows):
	buffer = io.StringIO()
	writer = csv.DictWriter(buffer, fieldnames=["barcode", "sku", "price", "active", "quantity"])
	writer.writeheader()
	for row in csv_rows:
		writer.writerow(
			{
				"barcode": row["barcode"],
				"sku": row.get("sku") or "",
				"price": row["price"],
				"active": int(row["active"]),
				"quantity": row["quantity"],
			}
		)
	return buffer.getvalue().encode("utf-8")


def _sanitize_error(message, password=None):
	text = str(message or "Unknown SFTP error")[:500]
	if password:
		text = text.replace(str(password), "[redacted]")
	lower = text.lower()
	for key in _SENSITIVE_KEYS:
		if key in lower and password:
			text = text.replace(str(password), "[redacted]")
	return text


def _load_sftp_settings(branch, require_enabled=False):
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Branch {0} does not exist").format(branch))

	values = frappe.db.get_value(
		"Branch",
		branch,
		[
			"custom_fp_sftp_enabled",
			"custom_fp_sftp_host",
			"custom_fp_sftp_port",
			"custom_fp_sftp_username",
			"custom_fp_sftp_remote_path",
		],
		as_dict=True,
	)
	if not values:
		frappe.throw(_("Branch {0} does not exist").format(branch))

	if require_enabled and not cint(values.custom_fp_sftp_enabled):
		frappe.throw(_("Foodpanda SFTP is not enabled for branch {0}").format(branch))

	host = (values.custom_fp_sftp_host or "").strip()
	username = (values.custom_fp_sftp_username or "").strip()
	remote_path = (values.custom_fp_sftp_remote_path or "").strip() or "/"
	port = cint(values.custom_fp_sftp_port) or 22

	branch_doc = frappe.get_doc("Branch", branch)
	password = branch_doc.get_password("custom_fp_sftp_password", raise_exception=False)

	missing = []
	if not host:
		missing.append(_("host"))
	if not username:
		missing.append(_("username"))
	if not password:
		missing.append(_("password"))
	if missing:
		frappe.throw(
			_("Foodpanda SFTP is missing {0} on branch {1}").format(
				frappe.utils.comma_and(missing), branch
			)
		)

	return {
		"host": host,
		"port": port,
		"username": username,
		"password": password,
		"remote_path": remote_path,
		"enabled": cint(values.custom_fp_sftp_enabled),
	}


def _remote_file_path(remote_path, filename):
	"""Build the remote target path.

	Blank remote path → put the file in the SFTP login home/cwd (filename only).
	Do not default to absolute ``/filename``: Foodpanda vendor accounts are usually
	chrooted and reject that with Errno 2.
	"""
	base = (remote_path or "").strip().rstrip("/")
	if not base or base == ".":
		return filename
	return f"{base}/{filename}"


def _sftp_put(settings, filename, csv_bytes):
	import paramiko

	transport = None
	try:
		transport = paramiko.Transport((settings["host"], settings["port"]))
		transport.connect(username=settings["username"], password=settings["password"])
		with closing(paramiko.SFTPClient.from_transport(transport)) as sftp:
			remote = _remote_file_path(settings["remote_path"], filename)
			with sftp.file(remote, "wb") as remote_file:
				remote_file.write(csv_bytes)
			return remote
	finally:
		if transport is not None:
			transport.close()


def _attach_csv(log_name, filename, csv_bytes):
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"content": csv_bytes,
			"attached_to_doctype": "Foodpanda SFTP Upload Log",
			"attached_to_name": log_name,
			"attached_to_field": "csv_file",
		}
	)
	file_doc.save(ignore_permissions=True)
	return file_doc.file_url


def _write_upload_log(
	*,
	branch,
	trigger,
	filename,
	row_count,
	skipped_count,
	status,
	error=None,
	csv_bytes=None,
):
	log = frappe.get_doc(
		{
			"doctype": "Foodpanda SFTP Upload Log",
			"branch": branch,
			"trigger": trigger,
			"filename": filename,
			"status": status,
			"run_by": frappe.session.user if frappe.session.user else "Administrator",
			"run_datetime": now_datetime(),
			"row_count": row_count,
			"skipped_count": skipped_count,
			"error": error,
		}
	)
	log.insert(ignore_permissions=True)
	if csv_bytes is not None:
		file_url = _attach_csv(log.name, filename, csv_bytes)
		frappe.db.set_value("Foodpanda SFTP Upload Log", log.name, "csv_file", file_url, update_modified=False)
		log.csv_file = file_url
	return log


def _update_branch_status(branch, *, success, error=None):
	values = {
		"custom_fp_sftp_last_error": error or "",
	}
	if success:
		values["custom_fp_sftp_last_upload"] = now_datetime()
	frappe.db.set_value("Branch", branch, values, update_modified=False)


def _sheet_rows_for_item_codes(branch, item_codes):
	# None = full sheet (Branch / scheduler). Explicit list (including empty) =
	# report-filtered set only — never expand an empty filter to the full catalog.
	if item_codes is None:
		return get_branch_price_sheet_rows(branch)
	wanted = {str(code).strip() for code in item_codes if str(code).strip()}
	if not wanted:
		return []
	return [row for row in get_branch_price_sheet_rows(branch) if row.get("item_code") in wanted]


def upload_foodpanda_csv(branch, rows=None, trigger=TRIGGER_BRANCH, require_enabled=False):
	"""Build CSV for ``branch`` and upload via that branch's SFTP settings.

	Returns a dict safe for Desk/RPC (no password). Raises on validation
	errors before connecting; connection failures are logged then re-raised.
	"""
	settings = _load_sftp_settings(branch, require_enabled=require_enabled)
	csv_rows, skipped = build_foodpanda_csv_rows(branch, rows=rows)
	filename = _csv_filename(branch)
	csv_bytes = build_foodpanda_csv_bytes(csv_rows)

	try:
		remote_path = _sftp_put(settings, filename, csv_bytes)
	except Exception as exc:
		error = _sanitize_error(exc, password=settings["password"])
		log = _write_upload_log(
			branch=branch,
			trigger=trigger,
			filename=filename,
			row_count=len(csv_rows),
			skipped_count=skipped,
			status="Failed",
			error=error,
			csv_bytes=csv_bytes,
		)
		_update_branch_status(branch, success=False, error=error)
		return {
			"branch": branch,
			"trigger": trigger,
			"filename": filename,
			"row_count": len(csv_rows),
			"skipped_count": skipped,
			"log": log.name,
			"status": "Failed",
			"error": error,
		}

	log = _write_upload_log(
		branch=branch,
		trigger=trigger,
		filename=filename,
		row_count=len(csv_rows),
		skipped_count=skipped,
		status="Success",
		csv_bytes=csv_bytes,
	)
	_update_branch_status(branch, success=True)
	return {
		"branch": branch,
		"trigger": trigger,
		"filename": filename,
		"remote_path": remote_path,
		"row_count": len(csv_rows),
		"skipped_count": skipped,
		"log": log.name,
		"status": "Success",
	}


@frappe.whitelist()
def upload_branch_foodpanda_csv(branch):
	"""Full-branch catalog upload (Branch form button)."""
	require_export_permission()
	if not branch:
		frappe.throw(_("Branch is required"))
	return upload_foodpanda_csv(branch, rows=None, trigger=TRIGGER_BRANCH, require_enabled=False)


@frappe.whitelist()
def upload_branch_price_sheet_foodpanda_csv(branch, item_codes=None):
	"""Report-scoped upload; ``item_codes`` limits to currently filtered rows."""
	require_export_permission()
	if not branch:
		frappe.throw(_("Branch is required"))
	if item_codes is not None and isinstance(item_codes, str):
		item_codes = frappe.parse_json(item_codes)
	rows = _sheet_rows_for_item_codes(branch, item_codes)
	return upload_foodpanda_csv(branch, rows=rows, trigger=TRIGGER_REPORT, require_enabled=False)


def _already_uploaded_today(branch, now=None):
	now = now or now_datetime()
	last_upload = frappe.db.get_value("Branch", branch, "custom_fp_sftp_last_upload")
	if not last_upload:
		return False
	return getdate(last_upload) == getdate(now)


def _is_branch_due_for_scheduled_upload(branch, now=None):
	"""True when enabled branch has a schedule time that is due and not yet done today."""
	now = now or now_datetime()
	values = frappe.db.get_value(
		"Branch",
		branch,
		["custom_fp_sftp_enabled", "custom_fp_sftp_schedule_time"],
		as_dict=True,
	)
	if not values or not cint(values.custom_fp_sftp_enabled):
		return False
	if not values.custom_fp_sftp_schedule_time:
		return False
	if _already_uploaded_today(branch, now=now):
		return False

	schedule_time = get_time(values.custom_fp_sftp_schedule_time)
	scheduled_at = get_datetime(f"{getdate(now)} {schedule_time}")
	return now >= scheduled_at


def run_scheduled_foodpanda_sftp_uploads():
	"""Upload each enabled Branch once its Branch schedule time is due today.

	Invoked about every 15 minutes. Skips branches with no schedule time, and
	skips any branch that already has a successful last_upload dated today.
	"""
	branches = frappe.get_all(
		"Branch",
		filters={"custom_fp_sftp_enabled": 1},
		pluck="name",
		order_by="name asc",
	)
	now = now_datetime()
	results = []
	for branch in branches:
		if not _is_branch_due_for_scheduled_upload(branch, now=now):
			results.append({"branch": branch, "status": "Skipped"})
			continue
		try:
			result = upload_foodpanda_csv(
				branch, rows=None, trigger=TRIGGER_SCHEDULER, require_enabled=True
			)
			if result.get("status") == "Failed":
				results.append(
					{"branch": branch, "status": "Failed", "log": result.get("log"), "error": result.get("error")}
				)
			else:
				results.append({"branch": branch, "status": "Success", "log": result["log"]})
		except Exception as exc:
			# Validation / unexpected errors before or around upload. Mid-upload
			# transport failures already return Failed above without raising.
			password = None
			try:
				password = frappe.get_doc("Branch", branch).get_password(
					"custom_fp_sftp_password", raise_exception=False
				)
			except Exception:
				password = None
			safe = _sanitize_error(exc, password=password)
			_write_upload_log(
				branch=branch,
				trigger=TRIGGER_SCHEDULER,
				filename=_csv_filename(branch),
				row_count=0,
				skipped_count=0,
				status="Failed",
				error=safe,
			)
			_update_branch_status(branch, success=False, error=safe)
			frappe.log_error(
				title=f"Foodpanda SFTP scheduled upload failed: {branch}",
				message=frappe.get_traceback(),
			)
			results.append({"branch": branch, "status": "Failed", "error": safe})
	return results
