import frappe
from frappe.utils import flt, now_datetime

# Legacy single global Foodpanda Item Price list name. No longer created by
# app code - Foodpanda pricing is per-branch now (see
# get_or_create_branch_foodpanda_price_list). Kept only so
# patches.create_branch_foodpanda_price_list can find and repoint whichever
# existing site already had one live, instead of creating a duplicate.
FOODPANDA_PRICE_LIST = "Foodpanda"
BRANCH_PRICE_LIST_SUFFIX = " Selling Price List"
BRANCH_FOODPANDA_PRICE_LIST_SUFFIX = " Foodpanda Price List"
BRANCH_BASELINE_FIELDS = (
	"item_code",
	"uom",
	"packing_unit",
	"item_name",
	"brand",
	"item_description",
	"customer",
	"batch_no",
	"currency",
	"price_list_rate",
	"custom_latest_price_incl_taxes",
	"custom_mrp",
	"valid_from",
	"lead_time_days",
	"valid_upto",
	"note",
	"reference",
)


def _default_currency():
	return frappe.db.get_single_value("Global Defaults", "default_currency")


def get_branch_price_list_name(branch):
	return f"{branch}{BRANCH_PRICE_LIST_SUFFIX}"


def get_branch_foodpanda_price_list_name(branch):
	return f"{branch}{BRANCH_FOODPANDA_PRICE_LIST_SUFFIX}"


def _copy_branch_price_baseline(source_price_list, target_price_list):
	"""Copy a validated selling baseline in batches without N document inserts."""
	rows = frappe.get_all(
		"Item Price",
		filters={"price_list": source_price_list, "selling": 1},
		fields=list(BRANCH_BASELINE_FIELDS),
	)
	if not rows:
		return 0

	timestamp = now_datetime()
	user = frappe.session.user or "Administrator"
	fields = [
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		*BRANCH_BASELINE_FIELDS,
		"price_list",
		"supplier",
		"buying",
		"selling",
	]
	values = [
		(
			frappe.generate_hash(length=10),
			user,
			timestamp,
			timestamp,
			user,
			0,
			index,
			*(row.get(field) for field in BRANCH_BASELINE_FIELDS),
			target_price_list,
			None,
			0,
			1,
		)
		for index, row in enumerate(rows, start=1)
	]
	frappe.db.bulk_insert("Item Price", fields, values, chunk_size=1000)
	return len(values)


def get_or_create_branch_price_list(branch):
	"""Return the Branch's selling-only Price List, creating and linking one
	during Branch initialization or as a safe fallback when first needed.

	On first creation, every active Item Price row from the global default
	Selling Settings.selling_price_list is copied in once so the branch
	starts from the existing price baseline, then every POS Profile for this
	branch is repointed onto the new list.
	"""
	existing = frappe.db.get_value("Branch", branch, "default_selling_price_list")
	if existing:
		_validate_selling_only_price_list(existing)
		return existing

	price_list_name = get_branch_price_list_name(branch)
	if not frappe.db.exists("Price List", price_list_name):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": price_list_name,
				"selling": 1,
				"buying": 0,
				"currency": _default_currency(),
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

		default_price_list = (
			frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
		)
		_copy_branch_price_baseline(default_price_list, price_list_name)
	else:
		_validate_selling_only_price_list(price_list_name)

	frappe.db.set_value("Branch", branch, "default_selling_price_list", price_list_name)

	# Food Panda profiles are excluded here - they belong on the branch's
	# Foodpanda list instead, via get_or_create_branch_foodpanda_price_list.
	for pos_profile in frappe.get_all(
		"POS Profile",
		filters={"branch": branch, "custom_is_foodpanda_profile": 0},
		pluck="name",
	):
		frappe.db.set_value("POS Profile", pos_profile, "selling_price_list", price_list_name)

	return price_list_name


def _validate_selling_only_price_list(price_list):
	details = frappe.db.get_value("Price List", price_list, ["selling", "buying", "enabled"], as_dict=True)
	if not details:
		frappe.throw(f"Branch Price List {price_list} does not exist.")
	if not details.selling or details.buying or not details.enabled:
		frappe.throw(f"Branch Price List {price_list} must be enabled, Selling, and not Buying.")


def get_or_create_branch_foodpanda_price_list(branch):
	"""Return the Branch's Foodpanda-only Price List, creating and linking
	one the first time it's needed. Mirrors get_or_create_branch_price_list,
	but no baseline copy on creation - Foodpanda pricing (MRP-as-rate) is
	seeded item-by-item from Purchase Receipts via apply_foodpanda_price_update,
	not from the branch's normal selling baseline.
	"""
	existing = frappe.db.get_value("Branch", branch, "default_foodpanda_price_list")
	if existing:
		_validate_selling_only_price_list(existing)
		return existing

	price_list_name = get_branch_foodpanda_price_list_name(branch)
	if not frappe.db.exists("Price List", price_list_name):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": price_list_name,
				"selling": 1,
				"buying": 0,
				"currency": _default_currency(),
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	else:
		_validate_selling_only_price_list(price_list_name)

	frappe.db.set_value("Branch", branch, "default_foodpanda_price_list", price_list_name)

	for pos_profile in frappe.get_all(
		"POS Profile",
		filters={"branch": branch, "custom_is_foodpanda_profile": 1},
		pluck="name",
	):
		frappe.db.set_value("POS Profile", pos_profile, "selling_price_list", price_list_name)

	return price_list_name


def log_price_update(purchase_receipt, item_code, price_list, branch, field_updated, old_value, new_value):
	"""One audit row per changed field. restore_prices_on_cancel relies on
	these to decide whether a cancel is still safe to roll back."""
	frappe.get_doc(
		{
			"doctype": "Item Price Update Log",
			"purchase_receipt": purchase_receipt,
			"item_code": item_code,
			"price_list": price_list,
			"branch": branch,
			"field_updated": field_updated,
			"old_value": flt(old_value),
			"new_value": flt(new_value),
			"updated_by": frappe.session.user,
			"update_datetime": now_datetime(),
		}
	).insert(ignore_permissions=True)


def upsert_item_price(item_code, price_list, purchase_receipt, branch=None, rate=None, mrp=None):
	"""Create or update the Item Price row for (item_code, price_list),
	logging every field that actually changes before overwriting it."""
	existing_name = frappe.db.get_value(
		"Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}, "name"
	)

	if existing_name:
		current = frappe.db.get_value(
			"Item Price", existing_name, ["price_list_rate", "custom_mrp"], as_dict=True
		)
		updates = {}
		if rate is not None and flt(current.price_list_rate) != flt(rate):
			log_price_update(
				purchase_receipt, item_code, price_list, branch, "Rate", current.price_list_rate, rate
			)
			updates["price_list_rate"] = rate
		if mrp is not None and flt(current.custom_mrp) != flt(mrp):
			log_price_update(purchase_receipt, item_code, price_list, branch, "MRP", current.custom_mrp, mrp)
			updates["custom_mrp"] = mrp
		if updates:
			frappe.db.set_value("Item Price", existing_name, updates)
		return existing_name

	doc = frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": item_code,
			"price_list": price_list,
			"selling": 1,
			"currency": _default_currency(),
			"price_list_rate": rate or 0,
			"custom_mrp": mrp or 0,
		}
	)
	doc.insert(ignore_permissions=True)
	if rate:
		log_price_update(purchase_receipt, item_code, price_list, branch, "Rate", 0, rate)
	if mrp:
		log_price_update(purchase_receipt, item_code, price_list, branch, "MRP", 0, mrp)
	return doc.name
