# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

"""Foodpanda vendor-automation SFTP CSV upload.

Builds the same `sku,barcode,price,active,quantity` shape as Branch Price
Sheet's Download Foodpanda CSV, then puts the file via site-wide SFTP
credentials on Foodpanda Settings. Filename is `{prefix}_{vendor_id}.csv`
as required by Foodpanda Catalog-SFTP. Per-outlet enable/schedule lives on
Foodpanda Outlet. This is the portal CSV path — not Partner API catalog
sync.
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
TRIGGER_OUTLET = "Outlet"

CSV_FIELDNAMES = ("sku", "barcode", "price", "active", "quantity")
DEFAULT_SFTP_PREFIX = "catalog"
DEFAULT_SFTP_REMOTE_PATH = "Catalog"

_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "sftp_password", "custom_fp_sftp_password")


def _primary_barcode(row):
	if row.get("barcode1"):
		return str(row["barcode1"]).strip()
	barcodes = row.get("_barcodes") or []
	if barcodes:
		return str(barcodes[0]).strip()
	return ""


def parse_inactive_if_qty_lte(value):
	"""Return a non-negative threshold, or None when the rule is off.

	Blank/None disables the rule (CSV active follows quantity > 0).
	"""
	if value is None or value == "":
		return None
	threshold = flt(value)
	if threshold < 0:
		return None
	return threshold


def resolve_foodpanda_active(quantity, inactive_if_qty_lte=None):
	"""Foodpanda CSV ``active`` flag from sellable qty and optional threshold.

	When ``inactive_if_qty_lte`` is set (e.g. 3), quantity at or below that
	threshold is inactive. Otherwise active means quantity > 0.
	"""
	quantity = max(flt(quantity), 0)
	threshold = parse_inactive_if_qty_lte(inactive_if_qty_lte)
	if threshold is None:
		return 1 if quantity > 0 else 0
	return 0 if quantity <= threshold else 1


def _sku_map_for_branch(branch):
	outlet_name = frappe.db.get_value("Foodpanda Outlet", {"branch": branch}, "name")
	if not outlet_name:
		return {}
	rows = frappe.get_all(
		"Foodpanda Product",
		filters={"outlet": outlet_name},
		fields=["item_code", "foodpanda_product_id"],
		ignore_permissions=True,
	)
	return {
		row.item_code: str(row.foodpanda_product_id).strip()
		for row in rows
		if row.item_code and row.foodpanda_product_id
	}


def build_foodpanda_csv_rows(branch, rows=None, inactive_if_qty_lte=None):
	"""Return (csv_rows, skipped_count) for the vendor-upload shape.

	When ``rows`` is None, load the full branch price sheet. Otherwise treat
	``rows`` as already-filtered sheet row dicts (report upload path).
	``inactive_if_qty_lte`` comes from Branch Price Sheet's report filter;
	Branch/scheduler uploads leave it unset.
	"""
	source_rows = rows if rows is not None else get_branch_price_sheet_rows(branch)
	sku_map = _sku_map_for_branch(branch)
	csv_rows = []
	skipped = 0
	for row in source_rows:
		barcode = _primary_barcode(row)
		price = flt(row.get("foodpanda_price"))
		if not barcode or price <= 0:
			skipped += 1
			continue
		quantity = max(int(flt(row.get("available_qty"))), 0)
		item_code = row.get("item_code")
		csv_rows.append(
			{
				"sku": sku_map.get(item_code) or "",
				"barcode": barcode,
				"price": price,
				"active": resolve_foodpanda_active(quantity, inactive_if_qty_lte),
				"quantity": quantity,
			}
		)
	return csv_rows, skipped


def build_foodpanda_csv_bytes(csv_rows):
	buffer = io.StringIO()
	writer = csv.DictWriter(buffer, fieldnames=list(CSV_FIELDNAMES))
	writer.writeheader()
	for row in csv_rows:
		writer.writerow(
			{
				"sku": row.get("sku") or "",
				"barcode": row["barcode"],
				"price": row["price"],
				"active": int(row["active"]),
				"quantity": int(row["quantity"]),
			}
		)
	return buffer.getvalue().encode("utf-8")


def sanitize_sftp_filename_prefix(prefix):
	"""Return a Foodpanda-safe prefix (no path, no .csv, default catalog)."""
	text = (prefix or "").strip()
	if text.lower().endswith(".csv"):
		text = text[:-4]
	text = text.replace(" ", "")
	if "/" in text or "\\" in text:
		frappe.throw(_("SFTP filename prefix cannot contain a path separator"))
	return text or DEFAULT_SFTP_PREFIX


def csv_filename(vendor_id, prefix=None):
	"""Foodpanda single-vendor Catalog-SFTP name: `{prefix}_{vendor_id}.csv`."""
	vendor = (vendor_id or "").strip()
	if not vendor:
		frappe.throw(_("Foodpanda Outlet is missing Vendor ID for the SFTP filename"))
	if "/" in vendor or "\\" in vendor:
		frappe.throw(_("Foodpanda Vendor ID cannot contain a path separator"))
	return f"{sanitize_sftp_filename_prefix(prefix)}_{vendor}.csv"


def _csv_filename(branch, prefix=None, vendor_id=None):
	"""Compatibility wrapper used by tests and scheduler error logs."""
	if not vendor_id:
		vendor_id = frappe.db.get_value("Foodpanda Outlet", {"branch": branch}, "vendor_id")
	return csv_filename(vendor_id, prefix=prefix)


def _sanitize_error(message, password=None):
	text = str(message or "Unknown SFTP error")[:500]
	if password:
		text = text.replace(str(password), "[redacted]")
	lower = text.lower()
	for key in _SENSITIVE_KEYS:
		if key in lower and password:
			text = text.replace(str(password), "[redacted]")
	return text


def _get_outlet_for_branch(branch):
	if not frappe.db.exists("Branch", branch):
		frappe.throw(_("Branch {0} does not exist").format(branch))
	outlet_name = frappe.db.get_value("Foodpanda Outlet", {"branch": branch}, "name")
	if not outlet_name:
		frappe.throw(_("No Foodpanda Outlet exists for branch {0}").format(branch))
	return frappe.get_doc("Foodpanda Outlet", outlet_name)


def _load_sftp_settings(branch, require_enabled=False):
	outlet = _get_outlet_for_branch(branch)
	if require_enabled and not cint(outlet.sftp_enabled):
		frappe.throw(_("Foodpanda SFTP is not enabled for branch {0}").format(branch))

	settings = frappe.get_single("Foodpanda Settings")
	host = (settings.sftp_host or "").strip()
	username = (settings.sftp_username or "").strip()
	remote_path = (settings.sftp_remote_path or "").strip() or DEFAULT_SFTP_REMOTE_PATH
	port = cint(settings.sftp_port) or 22
	prefix = outlet.sftp_filename_prefix or settings.sftp_filename_prefix
	password = settings.get_password("sftp_password", raise_exception=False)

	missing = []
	if not host:
		missing.append(_("host"))
	if not username:
		missing.append(_("username"))
	if not password:
		missing.append(_("password"))
	if missing:
		frappe.throw(
			_("Foodpanda SFTP is missing {0} on Foodpanda Settings").format(
				frappe.utils.comma_and(missing)
			)
		)

	return {
		"host": host,
		"port": port,
		"username": username,
		"password": password,
		"remote_path": remote_path,
		"filename_prefix": prefix,
		"vendor_id": (outlet.vendor_id or "").strip(),
		"outlet": outlet.name,
		"enabled": cint(outlet.sftp_enabled),
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


def _update_outlet_status(branch, *, success, error=None):
	outlet_name = frappe.db.get_value("Foodpanda Outlet", {"branch": branch}, "name")
	if not outlet_name:
		return
	values = {
		"sftp_last_error": error or "",
	}
	if success:
		values["sftp_last_upload"] = now_datetime()
	frappe.db.set_value("Foodpanda Outlet", outlet_name, values, update_modified=False)


def _sheet_rows_for_item_codes(branch, item_codes):
	# None = full sheet (Branch / scheduler). Explicit list (including empty) =
	# report-filtered set only — never expand an empty filter to the full catalog.
	if item_codes is None:
		return get_branch_price_sheet_rows(branch)
	wanted = {str(code).strip() for code in item_codes if str(code).strip()}
	if not wanted:
		return []
	return [row for row in get_branch_price_sheet_rows(branch) if row.get("item_code") in wanted]


def upload_foodpanda_csv(
	branch, rows=None, trigger=TRIGGER_BRANCH, require_enabled=False, inactive_if_qty_lte=None
):
	"""Build CSV for ``branch`` and upload via Foodpanda Settings SFTP.

	Returns a dict safe for Desk/RPC (no password). Raises on validation
	errors before connecting; connection failures are logged then re-raised.
	"""
	settings = _load_sftp_settings(branch, require_enabled=require_enabled)
	csv_rows, skipped = build_foodpanda_csv_rows(
		branch, rows=rows, inactive_if_qty_lte=inactive_if_qty_lte
	)
	filename = csv_filename(settings["vendor_id"], prefix=settings["filename_prefix"])
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
		_update_outlet_status(branch, success=False, error=error)
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
	_update_outlet_status(branch, success=True)
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
	"""Full-branch catalog upload (Branch / Outlet form button)."""
	require_export_permission()
	if not branch:
		frappe.throw(_("Branch is required"))
	return upload_foodpanda_csv(branch, rows=None, trigger=TRIGGER_BRANCH, require_enabled=False)


@frappe.whitelist()
def upload_outlet_foodpanda_csv(outlet):
	"""Full-outlet catalog upload (Foodpanda Outlet form button)."""
	require_export_permission()
	if not outlet:
		frappe.throw(_("Foodpanda Outlet is required"))
	branch = frappe.db.get_value("Foodpanda Outlet", outlet, "branch")
	if not branch:
		frappe.throw(_("Foodpanda Outlet {0} has no Branch").format(outlet))
	return upload_foodpanda_csv(branch, rows=None, trigger=TRIGGER_OUTLET, require_enabled=False)


@frappe.whitelist()
def upload_branch_price_sheet_foodpanda_csv(branch, item_codes=None, inactive_if_qty_lte=None):
	"""Report-scoped upload; ``item_codes`` limits to currently filtered rows."""
	require_export_permission()
	if not branch:
		frappe.throw(_("Branch is required"))
	if item_codes is not None and isinstance(item_codes, str):
		item_codes = frappe.parse_json(item_codes)
	rows = _sheet_rows_for_item_codes(branch, item_codes)
	return upload_foodpanda_csv(
		branch,
		rows=rows,
		trigger=TRIGGER_REPORT,
		require_enabled=False,
		inactive_if_qty_lte=inactive_if_qty_lte,
	)


def _already_uploaded_today(branch, now=None):
	now = now or now_datetime()
	last_upload = frappe.db.get_value("Foodpanda Outlet", {"branch": branch}, "sftp_last_upload")
	if not last_upload:
		return False
	return getdate(last_upload) == getdate(now)


def _is_branch_due_for_scheduled_upload(branch, now=None):
	"""True when enabled outlet has a schedule time that is due and not yet done today."""
	now = now or now_datetime()
	values = frappe.db.get_value(
		"Foodpanda Outlet",
		{"branch": branch},
		["sftp_enabled", "sftp_schedule_time"],
		as_dict=True,
	)
	if not values or not cint(values.sftp_enabled):
		return False
	if not values.sftp_schedule_time:
		return False
	if _already_uploaded_today(branch, now=now):
		return False

	schedule_time = get_time(values.sftp_schedule_time)
	scheduled_at = get_datetime(f"{getdate(now)} {schedule_time}")
	return now >= scheduled_at


def run_scheduled_foodpanda_sftp_uploads():
	"""Upload each enabled Foodpanda Outlet once its schedule time is due today.

	Invoked about every 15 minutes. Skips outlets with no schedule time, and
	skips any outlet that already has a successful last_upload dated today.
	"""
	outlets = frappe.get_all(
		"Foodpanda Outlet",
		filters={"sftp_enabled": 1},
		fields=["name", "branch", "vendor_id", "sftp_filename_prefix"],
		order_by="branch asc",
	)
	now = now_datetime()
	results = []
	for outlet in outlets:
		branch = outlet.branch
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
			password = None
			try:
				password = frappe.get_single("Foodpanda Settings").get_password(
					"sftp_password", raise_exception=False
				)
			except Exception:
				password = None
			safe = _sanitize_error(exc, password=password)
			try:
				failed_name = csv_filename(outlet.vendor_id, prefix=outlet.sftp_filename_prefix)
			except Exception:
				failed_name = f"{DEFAULT_SFTP_PREFIX}_unknown.csv"
			_write_upload_log(
				branch=branch,
				trigger=TRIGGER_SCHEDULER,
				filename=failed_name,
				row_count=0,
				skipped_count=0,
				status="Failed",
				error=safe,
			)
			_update_outlet_status(branch, success=False, error=safe)
			frappe.log_error(
				title=f"Foodpanda SFTP scheduled upload failed: {branch}",
				message=frappe.get_traceback(),
			)
			results.append({"branch": branch, "status": "Failed", "error": safe})
	return results
