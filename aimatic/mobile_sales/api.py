import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, now_datetime, nowdate

from aimatic.branch_management.utils import get_branch_defaults, get_user_default_branch, user_can_override


_OAUTH_APP = "Aimatic Sales Android"
_ALLOWED_ROLES = {"Sales User", "Sales Manager", "System Manager"}
_MAX_PAGE_LENGTH = 100


def _require_sales_user():
	user = frappe.session.user
	if user in (None, "", "Guest"):
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	if not _ALLOWED_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(_("A Sales User or Sales Manager role is required"), frappe.PermissionError)
	return user


def _page_length(value):
	return min(max(cint(value) or 20, 1), _MAX_PAGE_LENGTH)


def _permitted_branch(branch=None):
	default = get_user_default_branch()
	if branch:
		if branch != default and not user_can_override():
			frappe.throw(_("You are not permitted to use Branch {0}").format(branch), frappe.PermissionError)
		if not frappe.db.exists("Branch", branch):
			frappe.throw(_("Invalid Branch: {0}").format(branch))
		return branch
	return default or None


def _warehouse_doc(warehouse, company=None, required=False):
	if not warehouse:
		return None
	doc = frappe.get_doc("Warehouse", warehouse)
	try:
		doc.check_permission("read")
	except frappe.PermissionError:
		if required:
			raise
		return None
	if cint(doc.disabled) or cint(doc.is_group) or (company and doc.company != company):
		if required:
			frappe.throw(_("Warehouse {0} is not an active stock warehouse for Company {1}").format(warehouse, company))
		return None
	return doc


def _default_company(branch_doc=None, warehouse_doc=None, fallback_branch_doc=None):
	if warehouse_doc:
		return warehouse_doc.company
	if branch_doc:
		return branch_doc.company
	company = frappe.defaults.get_user_default("Company") or frappe.defaults.get_global_default("default_company")
	if company:
		return company
	if fallback_branch_doc:
		return fallback_branch_doc.company
	companies = frappe.get_list("Company", pluck="name", limit_page_length=2)
	if len(companies) == 1:
		return companies[0]
	frappe.throw(_("Select a default Company or Warehouse in ERPNext"))


def _default_warehouse(company, branch=None, requested=None, required=True):
	if requested:
		return _warehouse_doc(requested, company, required=True).name

	# Preserve ERPNext's standard user/global defaults before consulting Ai Matic Branch settings.
	for candidate in (
		frappe.defaults.get_user_default("Warehouse"),
		frappe.db.get_single_value("Stock Settings", "default_warehouse"),
	):
		doc = _warehouse_doc(candidate, company)
		if doc:
			return doc.name

	if branch:
		candidate = get_branch_defaults(branch).get("finished_goods_warehouse")
		doc = _warehouse_doc(candidate, company)
		if doc:
			return doc.name

	warehouses = frappe.get_list(
		"Warehouse",
		filters={"company": company, "disabled": 0, "is_group": 0},
		pluck="name",
		limit_page_length=2,
	)
	if len(warehouses) == 1:
		return warehouses[0]
	if required:
		frappe.throw(_("Select a Warehouse for Company {0}").format(company))
	return None


def _sales_context(branch=None, warehouse=None, require_warehouse=True):
	requested_branch = branch
	branch = _permitted_branch(branch)
	branch_doc = frappe.get_cached_doc("Branch", branch) if branch else None
	requested_warehouse = _warehouse_doc(warehouse, required=True) if warehouse else None
	company = _default_company(branch_doc if requested_branch else None, requested_warehouse, branch_doc)
	if branch_doc and branch_doc.company != company:
		branch = None
		branch_doc = None
	warehouse = _default_warehouse(company, branch, warehouse, require_warehouse)
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or (
		branch_doc.get("default_selling_price_list") if branch_doc else None
	)
	if not price_list:
		frappe.throw(_("Configure the Selling Price List in ERPNext Selling Settings"))
	return frappe._dict(
		branch=branch,
		company=company,
		warehouse=warehouse,
		price_list=price_list,
		currency=frappe.db.get_value("Price List", price_list, "currency")
		or frappe.db.get_value("Company", company, "default_currency"),
	)


def _customer_doc(customer):
	if not customer:
		frappe.throw(_("Customer is required"))
	doc = frappe.get_doc("Customer", customer)
	doc.check_permission("read")
	if cint(doc.disabled):
		frappe.throw(_("Customer {0} is disabled").format(customer))
	return doc


def _customer_price_list(customer, branch_price_list):
	return customer.default_price_list or frappe.db.get_value(
		"Customer Group", customer.customer_group, "default_price_list"
	) or branch_price_list


def _item_stock_and_rates(item_codes, warehouse, price_list):
	"""Per-item stock (base UOM) plus alternate-UOM price/conversion context.

	Returns (stock_by_item, rate_by_item, uom_by_item):
	- stock_by_item / rate_by_item keep their original shape (rate_by_item is the item's
	  price in its own stock_uom - previously this mixed rates from ANY uom together via a
	  single "latest modified, whichever uom" query, which is the same class of bug as the
	  missing UOM selector itself; fixed here as part of making per-UOM pricing correct).
	- uom_by_item: {item_code: {"default_uom": str, "uoms": [{uom, conversion_factor, rate}]}}.
	  A uom's rate prefers a dedicated Item Price row for that (item_code, uom, price_list);
	  falling back to stock_uom_rate * conversion_factor, mirroring ERPNext's own
	  get_price_list_rate fallback (erpnext/stock/get_item_details.py:1291) so client-shown
	  estimates agree with what set_missing_values/calculate_taxes_and_totals compute
	  server-side at submit time.
	"""
	if not item_codes:
		return {}, {}, {}

	bins = frappe.get_all(
		"Bin",
		filters={"item_code": ["in", item_codes], "warehouse": warehouse},
		fields=["item_code", "actual_qty", "reserved_qty"],
		limit_page_length=len(item_codes),
	)
	stock_by_item = {row.item_code: row for row in bins}

	items = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", "stock_uom", "sales_uom"],
		limit_page_length=len(item_codes),
	)
	conversions = frappe.get_all(
		"UOM Conversion Detail",
		filters={"parent": ["in", item_codes]},
		fields=["parent", "uom", "conversion_factor"],
		limit_page_length=max(len(item_codes) * 5, 100),
	)
	prices = frappe.get_all(
		"Item Price",
		filters={"item_code": ["in", item_codes], "price_list": price_list, "selling": 1},
		fields=["item_code", "uom", "price_list_rate"],
		order_by="modified desc",
		limit_page_length=max(len(item_codes) * 5, 100),
	)
	uom_rate_by_item = {}
	any_rate_by_item = {}
	for row in prices:
		if row.uom:
			uom_rate_by_item.setdefault((row.item_code, row.uom), flt(row.price_list_rate))
		any_rate_by_item.setdefault(row.item_code, flt(row.price_list_rate))

	factors_by_item = {}
	for row in conversions:
		factor = flt(row.conversion_factor)
		# Only expose alternate UOMs that are actually assigned to the Item and
		# have a usable conversion. Treating a blank/zero factor as 1 would make
		# an invalid packaging unit look orderable and price it like stock UOM.
		if row.uom and factor > 0:
			factors_by_item.setdefault(row.parent, {})[row.uom] = factor

	rate_by_item = {}
	uom_by_item = {}
	for item in items:
		stock_rate = uom_rate_by_item.get((item.name, item.stock_uom))
		if stock_rate is None:
			# No Item Price row explicitly tagged with the stock UOM - fall back to
			# whatever price exists for this item, same leniency the old code had.
			stock_rate = any_rate_by_item.get(item.name, 0)
		rate_by_item[item.name] = stock_rate

		factors = dict(factors_by_item.get(item.name, {}))
		factors.setdefault(item.stock_uom, 1.0)
		uom_by_item[item.name] = {
			"default_uom": item.sales_uom if item.sales_uom in factors else item.stock_uom,
			"uoms": [
				{
					"uom": uom,
					"conversion_factor": factor,
					"rate": uom_rate_by_item.get((item.name, uom), round(stock_rate * factor, 2)),
				}
				for uom, factor in factors.items()
			],
		}

	return stock_by_item, rate_by_item, uom_by_item


def _valid_item_uoms(item_code):
	"""The set of UOMs a Sales Order line for this item may legally use."""
	stock_uom = frappe.db.get_value("Item", item_code, "stock_uom")
	alternates = frappe.get_all(
		"UOM Conversion Detail",
		filters={"parent": item_code},
		pluck="uom",
	)
	return {stock_uom, *alternates} - {None}


def _credit_context(customer, company):
	from erpnext.accounts.utils import get_balance_on
	from erpnext.selling.doctype.customer.customer import get_credit_limit

	outstanding = flt(get_balance_on(party_type="Customer", party=customer, company=company))
	credit_limit = flt(get_credit_limit(customer, company))
	return {
		"outstanding_balance": outstanding,
		"credit_limit": credit_limit,
		"available_credit": max(credit_limit - outstanding, 0) if credit_limit else None,
	}


def _parse_items(items):
	if isinstance(items, str):
		try:
			items = json.loads(items)
		except (TypeError, ValueError):
			frappe.throw(_("items must be valid JSON"))
	if not isinstance(items, list) or not items:
		frappe.throw(_("At least one item is required"))
	result = []
	for value in items:
		if not isinstance(value, dict) or not value.get("item_code") or flt(value.get("qty")) <= 0:
			frappe.throw(_("Every item requires an item_code and quantity greater than zero"))
		item_code = value["item_code"]
		uom = value.get("uom")
		if uom and uom not in _valid_item_uoms(item_code):
			frappe.throw(_("{0} is not a valid unit of measure for item {1}").format(uom, item_code))
		result.append({
			"item_code": item_code,
			"qty": flt(value["qty"]),
			"uom": uom,
			"delivery_date": value.get("delivery_date"),
		})
	return result


def _make_order(branch, warehouse, customer, items, delivery_date=None, po_no=None, remarks=None):
	context = _sales_context(branch, warehouse)
	customer_doc = _customer_doc(customer)
	price_list = _customer_price_list(customer_doc, context.price_list)
	delivery_date = delivery_date or add_days(nowdate(), 1)
	values = {
		"doctype": "Sales Order",
		"customer": customer_doc.name,
		"company": context.company,
		"set_warehouse": context.warehouse,
		"selling_price_list": price_list,
		"delivery_date": delivery_date,
		"po_no": po_no,
		"remarks": remarks,
		"order_type": "Sales",
	}
	if context.branch:
		values["branch"] = context.branch
	doc = frappe.get_doc(values)
	for item in _parse_items(items):
		doc.append("items", {
			"item_code": item["item_code"],
			"qty": item["qty"],
			"uom": item["uom"],
			"delivery_date": item["delivery_date"] or delivery_date,
			"warehouse": context.warehouse,
		})
	doc.run_method("set_missing_values")
	doc.run_method("calculate_taxes_and_totals")
	return doc, context, _credit_context(customer_doc.name, context.company)


def _order_response(doc, credit=None, duplicate=False):
	credit = credit or _credit_context(doc.customer, doc.company)
	return {
		"sales_order": doc.name,
		"status": doc.status,
		"docstatus": doc.docstatus,
		"customer": doc.customer,
		"branch": doc.get("branch"),
		"warehouse": doc.set_warehouse,
		"price_list": doc.selling_price_list,
		"currency": doc.currency,
		"net_total": flt(doc.net_total, 2),
		"grand_total": flt(doc.grand_total, 2),
		"delivery_date": str(doc.delivery_date),
		"outstanding_balance": credit["outstanding_balance"],
		"credit_limit": credit["credit_limit"],
		"credit_warning": bool(credit["credit_limit"] and credit["outstanding_balance"] + flt(doc.grand_total) > credit["credit_limit"]),
		"duplicate": duplicate,
		"items": [{
			"item_code": row.item_code,
			"item_name": row.item_name,
			"qty": flt(row.qty),
			"uom": row.uom,
			"rate": flt(row.rate, 2),
			"amount": flt(row.amount, 2),
			"warehouse": row.warehouse,
		} for row in doc.items],
	}


@frappe.whitelist(allow_guest=True)
def get_public_config():
	client = frappe.db.get_value("OAuth Client", {"app_name": _OAUTH_APP}, ["name", "default_redirect_uri"], as_dict=True)
	if not client:
		frappe.throw(_("Ai Matic Sales OAuth is not configured"))
	return {"oauth_client_id": client.name, "redirect_uri": client.default_redirect_uri, "scope": "sales-ordering"}


@frappe.whitelist()
def get_context(branch=None, warehouse=None):
	user = _require_sales_user()
	# Initial login must return the available choices even when ERPNext has
	# several warehouses and no default. Transaction endpoints stay strict.
	context = _sales_context(branch, warehouse, require_warehouse=False)
	default_branch = get_user_default_branch()
	branches = frappe.get_list("Branch", fields=["name", "company"], order_by="name", limit_page_length=500) if user_can_override() else (
		[{"name": default_branch, "company": frappe.db.get_value("Branch", default_branch, "company")}] if default_branch else []
	)
	warehouses = frappe.get_list("Warehouse", filters={"company": context.company, "disabled": 0, "is_group": 0}, fields=["name", "company"], order_by="name", limit_page_length=500)
	return {"user": user, "full_name": frappe.utils.get_fullname(user), "roles": frappe.get_roles(user), "branches": branches, "warehouses": warehouses, **context}


@frappe.whitelist()
def search_customers(search=None, offset=0, limit=20):
	_require_sales_user()
	filters = {"disabled": 0}
	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = {"name": ["like", like], "customer_name": ["like", like], "mobile_no": ["like", like], "email_id": ["like", like]}
	return {"customers": frappe.get_list("Customer", filters=filters, or_filters=or_filters, fields=["name", "customer_name", "customer_group", "territory", "mobile_no", "email_id", "default_price_list"], start=cint(offset), page_length=_page_length(limit), order_by="customer_name")}


@frappe.whitelist()
def get_customer_context(customer, branch=None, warehouse=None):
	_require_sales_user()
	context = _sales_context(branch, warehouse)
	doc = _customer_doc(customer)
	credit = _credit_context(doc.name, context.company)
	return {"name": doc.name, "customer_name": doc.customer_name, "mobile_no": doc.mobile_no, "email_id": doc.email_id, "territory": doc.territory, "customer_group": doc.customer_group, "price_list": _customer_price_list(doc, context.price_list), **credit}


@frappe.whitelist()
def search_items(branch=None, warehouse=None, customer=None, search=None, barcode=None, offset=0, limit=30):
	_require_sales_user()
	context = _sales_context(branch, warehouse)
	customer_doc = _customer_doc(customer) if customer else None
	price_list = _customer_price_list(customer_doc, context.price_list) if customer_doc else context.price_list
	if barcode:
		codes = frappe.get_list("Item Barcode", filters={"barcode": barcode}, pluck="parent", limit_page_length=10)
		filters = {"name": ["in", codes or [""]], "disabled": 0, "is_sales_item": 1}
	else:
		filters = {"disabled": 0, "is_sales_item": 1}
	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = {
			"name": ["like", like],
			"item_name": ["like", like],
			"item_group": ["like", like],
			"brand": ["like", like],
		}
	rows = frappe.get_list("Item", filters=filters, or_filters=or_filters, fields=["name", "item_name", "item_group", "brand", "stock_uom", "image"], start=cint(offset), page_length=_page_length(limit), order_by="item_name")
	stock_by_item, rate_by_item, uom_by_item = _item_stock_and_rates([row.name for row in rows], context.warehouse, price_list)
	for row in rows:
		stock = stock_by_item.get(row.name) or {}
		available_qty = flt(stock.get("actual_qty")) - flt(stock.get("reserved_qty"))
		uom_info = uom_by_item.get(row.name) or {"default_uom": row.stock_uom, "uoms": [{"uom": row.stock_uom, "conversion_factor": 1.0, "rate": rate_by_item.get(row.name, 0)}]}
		for entry in uom_info["uoms"]:
			entry["available_qty"] = flt(available_qty / entry["conversion_factor"]) if entry["conversion_factor"] else 0
		row.update({
			"warehouse": context.warehouse,
			"actual_qty": flt(stock.get("actual_qty")),
			"available_qty": available_qty,
			"price_list": price_list,
			"rate": rate_by_item.get(row.name, 0),
			"default_uom": uom_info["default_uom"],
			"uoms": uom_info["uoms"],
		})
	return {"items": rows, "price_list": price_list, "warehouse": context.warehouse, "currency": context.currency}


@frappe.whitelist()
def preview_order(customer, items, branch=None, warehouse=None, delivery_date=None, po_no=None, remarks=None):
	_require_sales_user()
	doc, _context, credit = _make_order(branch, warehouse, customer, items, delivery_date, po_no, remarks)
	return _order_response(doc, credit)


@frappe.whitelist()
def create_order(request_id, customer, items, branch=None, warehouse=None, delivery_date=None, po_no=None, remarks=None):
	user = _require_sales_user()
	if not request_id or len(request_id) > 140:
		frappe.throw(_("A valid request_id is required"))
	if not frappe.has_permission("Sales Order", "create"):
		frappe.throw(_("Not permitted to create Sales Orders"), frappe.PermissionError)
	existing = frappe.db.get_value("Mobile Sales Order Request", request_id, ["user", "sales_order"], as_dict=True)
	if existing:
		if existing.user != user:
			frappe.throw(_("This request ID belongs to another user"), frappe.PermissionError)
		if existing.sales_order:
			doc = frappe.get_doc("Sales Order", existing.sales_order)
			doc.check_permission("read")
			return _order_response(doc, duplicate=True)
		frappe.throw(_("This request is already being processed"))
	frappe.get_doc({"doctype": "Mobile Sales Order Request", "request_id": request_id, "user": user, "status": "Processing", "created_at": now_datetime()}).insert(ignore_permissions=True)
	doc, _context, credit = _make_order(branch, warehouse, customer, items, delivery_date, po_no, remarks)
	doc.insert()
	frappe.db.set_value("Mobile Sales Order Request", request_id, {"sales_order": doc.name, "status": "Created"})
	return _order_response(doc, credit)


@frappe.whitelist()
def update_order(order, items, delivery_date=None, po_no=None, remarks=None):
	"""Edits an existing Sales Order still sitting as a Draft (docstatus=0).
	Only reachable for Draft orders - once submitted/cancelled, ERPNext's own
	docstatus immutability is the guard, not anything here. Recomputes items/
	pricing/tax the same way _make_order does for a new order (never trusts
	a client-supplied rate/amount), using the order's own existing branch/
	warehouse/price list rather than accepting them from the client, since
	those were already fixed when the order was first created.
	"""
	_require_sales_user()
	doc = frappe.get_doc("Sales Order", order)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only Draft orders can be edited - {0} has already been submitted or cancelled").format(order))

	warehouse = doc.set_warehouse
	doc.set("items", [])
	for item in _parse_items(items):
		doc.append("items", {
			"item_code": item["item_code"],
			"qty": item["qty"],
			"uom": item["uom"],
			"delivery_date": item["delivery_date"] or delivery_date or doc.delivery_date,
			"warehouse": warehouse,
		})
	if delivery_date:
		doc.delivery_date = delivery_date
	if po_no is not None:
		doc.po_no = po_no
	if remarks is not None:
		doc.remarks = remarks
	doc.run_method("set_missing_values")
	doc.run_method("calculate_taxes_and_totals")
	doc.save()
	return _order_response(doc)


@frappe.whitelist()
def get_orders(customer=None, offset=0, limit=20):
	_require_sales_user()
	filters = {"customer": customer} if customer else {}
	return {"orders": frappe.get_list("Sales Order", filters=filters, fields=["name", "customer", "customer_name", "transaction_date", "delivery_date", "status", "currency", "grand_total", "branch", "set_warehouse", "modified"], start=cint(offset), page_length=_page_length(limit), order_by="modified desc")}


@frappe.whitelist()
def get_order(order):
	_require_sales_user()
	doc = frappe.get_doc("Sales Order", order)
	doc.check_permission("read")
	return _order_response(doc)
