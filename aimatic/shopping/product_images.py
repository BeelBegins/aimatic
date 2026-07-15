import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import frappe
from frappe import _
from frappe.utils.file_manager import save_file


_CACHE_PREFIX = "aimatic:shopping:bgremove:"
_MAX_IMAGE_BYTES = 12 * 1024 * 1024
_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_DEFAULT_PYTHON = "/home/nabeel/.local/share/aimatic-bgremove/venv/bin/python"


def _require_manager():
	frappe.only_for("System Manager")


def _cache_key(job_id):
	return f"{_CACHE_PREFIX}{job_id}"


def _set_status(job_id, values):
	current = _get_status(job_id, check_owner=False) or {}
	current.update(values)
	frappe.cache.set_value(_cache_key(job_id), json.dumps(current), expires_in_sec=24 * 60 * 60)


def _get_status(job_id, check_owner=True):
	raw = frappe.cache.get_value(_cache_key(job_id))
	if not raw:
		return None
	if isinstance(raw, bytes):
		raw = raw.decode()
	value = json.loads(raw)
	if check_owner and value.get("owner") != frappe.session.user:
		frappe.throw(_("This image-processing job is not available to you"), frappe.PermissionError)
	return value


def _source_file(file_name, product):
	file_doc = frappe.get_doc("File", file_name)
	if file_doc.attached_to_doctype != "Shopping Product" or file_doc.attached_to_name != product:
		frappe.throw(_("Upload the image against the selected Shopping Product"))
	extension = Path(file_doc.file_name or file_doc.file_url or "").suffix.lower()
	if extension not in _ALLOWED_EXTENSIONS:
		frappe.throw(_("Use a JPG, PNG, or WebP image"))
	path = Path(file_doc.get_full_path()).resolve(strict=True)
	if path.stat().st_size > _MAX_IMAGE_BYTES:
		frappe.throw(_("The source image must be 12 MB or smaller"))
	return file_doc, path


@frappe.whitelist()
def list_products(search=None):
	_require_manager()
	filters = {"enabled": 1}
	rows = frappe.get_all(
		"Shopping Product",
		filters=filters,
		fields=["name", "item", "public_name", "category", "image", "modified"],
		order_by="modified desc",
		limit_page_length=100,
	)
	if search:
		query = search.strip().lower()
		rows = [row for row in rows if query in f"{row.item} {row.public_name or ''} {row.category or ''}".lower()]
	return {"products": rows}


@frappe.whitelist()
def start_background_removal(product, file_name):
	_require_manager()
	if not frappe.db.exists("Shopping Product", product):
		frappe.throw(_("Shopping Product does not exist"))
	file_doc, _path = _source_file(file_name, product)
	job_id = uuid.uuid4().hex
	_set_status(job_id, {
		"job_id": job_id,
		"owner": frappe.session.user,
		"product": product,
		"source_file": file_doc.name,
		"source_url": file_doc.file_url,
		"status": "queued",
		"message": _("Waiting for the image processor"),
	})
	frappe.enqueue(
		"aimatic.shopping.product_images.process_background_removal",
		queue="long",
		job_id=job_id,
		enqueue_after_commit=True,
		job_name=f"Shopping background removal {product}",
	)
	return _get_status(job_id)


@frappe.whitelist()
def get_background_removal_status(job_id):
	_require_manager()
	status = _get_status(job_id)
	if not status:
		frappe.throw(_("Image-processing job expired or was not found"))
	return status


@frappe.whitelist()
def approve_background_removal(job_id):
	_require_manager()
	status = _get_status(job_id)
	if not status or status.get("status") != "ready" or not status.get("result_file"):
		frappe.throw(_("The processed image is not ready for approval"))
	product = frappe.get_doc("Shopping Product", status["product"])
	result = frappe.get_doc("File", status["result_file"])
	if result.attached_to_doctype != "Shopping Product" or result.attached_to_name != product.name:
		frappe.throw(_("Processed image attachment does not match this product"), frappe.PermissionError)
	product.image = result.file_url
	product.save()
	_set_status(job_id, {"status": "approved", "message": _("Product image updated"), "result_url": result.file_url})
	return {"product": product.name, "image": result.file_url, "status": "approved"}


def process_background_removal(job_id):
	status = _get_status(job_id, check_owner=False)
	if not status:
		return
	output_path = None
	try:
		_set_status(job_id, {"status": "processing", "message": _("Removing the background")})
		_file, source_path = _source_file(status["source_file"], status["product"])
		python = frappe.conf.get("aimatic_background_remover_python") or _DEFAULT_PYTHON
		worker = Path(__file__).with_name("background_remover_worker.py")
		if not Path(python).is_file():
			raise RuntimeError("Background remover environment is not installed")
		fd, output_path = tempfile.mkstemp(prefix="aimatic-bg-", suffix=".png")
		os.close(fd)
		subprocess.run(
			[python, str(worker), str(source_path), output_path, "--model", "u2netp"],
			check=True,
			capture_output=True,
			text=True,
			timeout=180,
			env={**os.environ, "U2NET_HOME": "/home/nabeel/.local/share/aimatic-bgremove/models", "OMP_NUM_THREADS": "4"},
		)
		with open(output_path, "rb") as handle:
			content = handle.read()
		result = save_file(
			f"{frappe.scrub(status['product'])}-transparent.png",
			content,
			"Shopping Product",
			status["product"],
			is_private=0,
		)
		_set_status(job_id, {
			"status": "ready",
			"message": _("Background removed. Review the result before saving."),
			"result_file": result.name,
			"result_url": result.file_url,
		})
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Shopping background removal failed")
		_set_status(job_id, {"status": "failed", "message": _("Background removal failed. Try another image or contact the administrator.")})
		raise
	finally:
		if output_path:
			Path(output_path).unlink(missing_ok=True)
