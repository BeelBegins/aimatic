import frappe

_DEBOUNCE_CACHE_PREFIX = "aimatic:foodpanda:availdebounce:"
_DEBOUNCE_SECONDS = 30


def on_bin_update(doc, method=None):
	"""Debounced trigger: a rapid run of stock moves on the same item/branch
	(receiving, then several POS sales) would otherwise fan out into one
	Foodpanda call per move. The cache key suppresses re-enqueue for
	_DEBOUNCE_SECONDS after the first one, so only the freshest qty gets
	pushed once things settle - mirrors the job-status cache idiom in
	aimatic.shopping.product_images, but as a plain debounce flag.
	"""
	if not doc.item_code or not doc.warehouse:
		return

	branch = frappe.db.get_value("Warehouse", doc.warehouse, "custom_branch")
	if not branch:
		return

	if not frappe.db.exists("Foodpanda Outlet", {"branch": branch, "catalog_sync_enabled": 1}):
		return

	cache_key = f"{_DEBOUNCE_CACHE_PREFIX}{branch}:{doc.item_code}"
	if frappe.cache.get_value(cache_key):
		return
	frappe.cache.set_value(cache_key, "1", expires_in_sec=_DEBOUNCE_SECONDS)

	frappe.enqueue(
		"aimatic.foodpanda_integration.catalog.sync_availability",
		queue="short",
		item_code=doc.item_code,
		branch=branch,
		enqueue_after_commit=True,
		job_name=f"Foodpanda availability sync {doc.item_code}",
	)
