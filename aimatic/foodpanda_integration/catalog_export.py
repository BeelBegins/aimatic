import gzip
import io
import json

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

from aimatic.foodpanda_integration import client
from aimatic.foodpanda_integration.client import FoodpandaAPIError


def normalize_remote_product(row):
	"""Normalize one Foodpanda catalog row from GET or export download."""
	if not isinstance(row, dict):
		return None
	sku = row.get("sku") or row.get("product_sku") or row.get("item_sku") or row.get("SKU")
	barcodes = row.get("barcodes")
	if barcodes is None and row.get("barcode"):
		barcodes = row.get("barcode")
	if barcodes is not None and not isinstance(barcodes, list):
		barcodes = [barcodes]
	title = row.get("title")
	if isinstance(title, dict):
		title = next(iter(title.values()), "")
	return {
		"sku": str(sku or "").strip(),
		"barcodes": [str(b).strip() for b in (barcodes or []) if b],
		"title": title or "",
		"price": row.get("price"),
		"active": row.get("active"),
	}


def parse_export_payload(raw):
	"""Parse Foodpanda catalog export bytes into normalized product dicts.

	Partner export downloads are typically CSV with columns
	``sku,barcode,price,active,maximum_sales_quantity``. JSON / JSONL /
	gzip are also accepted.
	"""
	if not raw:
		return []
	if isinstance(raw, str):
		raw = raw.encode("utf-8")
	if raw[:2] == b"\x1f\x8b":
		raw = gzip.decompress(raw)

	text = raw.decode("utf-8-sig", errors="replace").strip()
	if not text:
		return []

	# CSV export (quoted headers like "sku","barcode",...)
	first_line = text.splitlines()[0].strip()
	first_lower = first_line.lower().replace('"', "")
	looks_like_json = first_line.startswith("{") or first_line.startswith("[")
	if not looks_like_json and (
		first_lower.startswith("sku,") or first_lower.startswith("sku;") or first_lower.split(",")[0] == "sku"
	):
		return _parse_export_csv(text)

	products = []
	try:
		data = json.loads(text)
	except (TypeError, ValueError):
		for line in text.splitlines():
			line = line.strip()
			if not line:
				continue
			try:
				row = json.loads(line)
			except (TypeError, ValueError):
				continue
			normalized = normalize_remote_product(row)
			if normalized and normalized.get("sku"):
				products.append(normalized)
		return products

	if isinstance(data, list):
		rows = data
	elif isinstance(data, dict):
		rows = data.get("products") or data.get("items") or data.get("catalog") or []
	else:
		rows = []

	for row in rows:
		normalized = normalize_remote_product(row)
		if normalized and normalized.get("sku"):
			products.append(normalized)
	return products


def _parse_export_csv(text):
	import csv

	reader = csv.DictReader(io.StringIO(text))
	products = []
	for row in reader:
		if not isinstance(row, dict):
			continue
		# Normalize CSV keys (strip BOM/spaces/quotes leftovers).
		cleaned = {
			(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items()
		}
		sku = cleaned.get("sku") or cleaned.get("product_sku") or cleaned.get("item_sku")
		barcode = cleaned.get("barcode") or cleaned.get("barcodes")
		barcodes = []
		if barcode:
			if isinstance(barcode, str) and ("," in barcode or ";" in barcode):
				barcodes = [b.strip() for b in barcode.replace(";", ",").split(",") if b.strip()]
			else:
				barcodes = [str(barcode).strip()]
		active_raw = cleaned.get("active")
		active = None
		if active_raw is not None and str(active_raw) != "":
			active = str(active_raw).strip().lower() in {"1", "true", "yes", "y"}
		price = cleaned.get("price")
		try:
			price = float(price) if price not in (None, "") else None
		except (TypeError, ValueError):
			pass
		normalized = {
			"sku": str(sku or "").strip(),
			"barcodes": barcodes,
			"title": cleaned.get("title") or cleaned.get("name") or "",
			"price": price,
			"active": active,
		}
		if normalized["sku"]:
			products.append(normalized)
	return products


def fetch_download_url(download_url, timeout=120):
	"""GET a Foodpanda export file.

	Export callbacks return either a Partner API path on
	``foodpanda.partner.deliveryhero.io`` (needs OAuth) or a short-lived
	presigned URL (no OAuth).
	"""
	if not download_url:
		raise FoodpandaAPIError("Foodpanda catalog export is missing download_url")

	settings = client.get_settings()
	host = (settings.api_host or "").rstrip("/")
	if host and download_url.startswith(host):
		path = download_url[len(host) :] or "/"
		response = client.request("GET", path, settings=settings)
		return response.content

	try:
		response = requests.get(download_url, timeout=timeout)
	except requests.RequestException as error:
		raise FoodpandaAPIError(
			f"Foodpanda catalog export download failed: {error.__class__.__name__}"
		) from error
	if not response.ok:
		raise FoodpandaAPIError(
			f"Foodpanda catalog export download failed with HTTP {response.status_code}",
			status_code=response.status_code,
			response_body={"raw_response": (response.text or "")[:2000]},
		)
	return response.content


def _latest_export_job(outlet_name):
	rows = frappe.get_all(
		"Foodpanda Catalog Job",
		filters={"outlet": outlet_name, "operation": "Export", "status": "Completed"},
		fields=["name", "job_id", "export_file", "product_count", "completed_at"],
		order_by="completed_at desc, creation desc",
		limit=1,
	)
	return rows[0] if rows else None


def get_cached_export_meta(outlet_name):
	job = _latest_export_job(outlet_name)
	if not job or not job.export_file:
		return None
	return job


def iter_cached_export_products(outlet_name):
	"""Yield products from the latest completed export file for an outlet."""
	job = _latest_export_job(outlet_name)
	if not job or not job.export_file:
		return
	content = _read_attached_file(job.export_file)
	for product in parse_export_payload(content):
		yield product


def cached_export_product_count(outlet_name):
	job = _latest_export_job(outlet_name)
	if not job:
		return 0
	if job.product_count:
		return int(job.product_count)
	if not job.export_file:
		return 0
	return len(parse_export_payload(_read_attached_file(job.export_file)))


def _read_attached_file(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	return file_doc.get_content()


def attach_export_file(job_name, content, outlet_name):
	from frappe.utils.file_manager import save_file

	safe_outlet = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in outlet_name)[:40]
	filename = f"foodpanda-catalog-export-{safe_outlet}-{now_datetime().strftime('%Y%m%d-%H%M%S')}.json"
	saved = save_file(
		filename,
		content,
		"Foodpanda Catalog Job",
		job_name,
		is_private=1,
	)
	return saved.file_url


def download_and_store_export(foodpanda_job_id=None, job_id=None):
	"""Download export payload for one Foodpanda Catalog Job and attach it.

	Accept ``foodpanda_job_id`` (preferred). Do not pass ``job_id=`` into
	``frappe.enqueue`` — RQ reserves that for the Redis job identity.
	"""
	job_id = foodpanda_job_id or job_id
	if not job_id:
		return {"status": "Skipped", "reason": "Missing job_id"}

	job_name = frappe.db.get_value("Foodpanda Catalog Job", {"job_id": job_id}, "name")
	if not job_name:
		return {"status": "Skipped", "reason": "Job not found"}

	job = frappe.get_doc("Foodpanda Catalog Job", job_name)
	if job.export_file:
		return {"status": "Skipped", "reason": "Export file already stored", "job_id": job_id}
	if not job.download_url:
		# Webhook may not have arrived yet; try Partner job status API.
		try:
			from aimatic.foodpanda_integration import catalog

			catalog.refresh_remote_job(job_id)
			job.reload()
		except FoodpandaAPIError:
			pass
	if not job.download_url:
		return {"status": "Pending", "reason": "download_url not ready", "job_id": job_id}

	raw = fetch_download_url(job.download_url)
	products = parse_export_payload(raw)
	file_url = attach_export_file(job_name, raw, job.outlet or job.vendor_id or job_name)
	job.db_set(
		{
			"export_file": file_url,
			"product_count": len(products),
		}
	)
	if job.outlet:
		frappe.db.set_value(
			"Foodpanda Outlet",
			job.outlet,
			{
				"last_catalog_import_job": job.name,
				"last_catalog_import_at": now_datetime(),
				"remote_sku_count": len(products),
			},
		)
		if frappe.db.get_value("Foodpanda Outlet", job.outlet, "auto_map_on_catalog_import"):
			frappe.enqueue(
				"aimatic.foodpanda_integration.catalog.map_remote_catalog_by_barcode",
				queue="long",
				outlet_name=job.outlet,
				source="export",
				enqueue_after_commit=True,
				job_name=f"Foodpanda auto-map {job.outlet}",
				timeout=7200,
			)
	return {
		"status": "Completed",
		"job_id": job_id,
		"product_count": len(products),
		"export_file": file_url,
	}


def wait_for_export_ready(job_id, max_wait_seconds=1800, poll_seconds=10):
	"""Poll until export download_url or export_file is available."""
	import time

	deadline = time.time() + max_wait_seconds
	while time.time() < deadline:
		job = frappe.db.get_value(
			"Foodpanda Catalog Job",
			{"job_id": job_id},
			["name", "status", "download_url", "export_file"],
			as_dict=True,
		)
		if not job:
			frappe.throw(_("Foodpanda catalog export job was not found"))
		if job.export_file:
			return job
		if job.download_url and job.status == "Completed":
			result = download_and_store_export(job_id)
			if result.get("status") == "Completed":
				return frappe.db.get_value(
					"Foodpanda Catalog Job",
					job.name,
					["name", "status", "download_url", "export_file"],
					as_dict=True,
				)
		if job.status == "Failed":
			frappe.throw(_("Foodpanda catalog export job failed"))
		try:
			from aimatic.foodpanda_integration import catalog

			catalog.refresh_remote_job(job_id)
		except FoodpandaAPIError:
			pass
		time.sleep(poll_seconds)
	frappe.throw(_("Timed out waiting for Foodpanda catalog export"))
