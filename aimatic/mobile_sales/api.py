import json

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, getdate, now_datetime, nowdate

from aimatic.branch_management.utils import get_branch_defaults, get_user_default_branch, user_can_override


_OAUTH_APP = "Aimatic Sales Android"
_ALLOWED_ROLES = {"Sales User", "Sales Manager", "System Manager"}
_MAX_PAGE_LENGTH = 100
_DELIVERY_DAY_FIELDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _require_sales_user():
	user = frappe.session.user
	if user in (None, "", "Guest"):
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	if not _ALLOWED_ROLES.intersection(frappe.get_roles(user)):
		frappe.throw(_("A Sales User or Sales Manager role is required"), frappe.PermissionError)
	return user


def _require_sales_manager():
	user = _require_sales_user()
	if not {"Sales Manager", "System Manager"}.intersection(frappe.get_roles(user)):
		frappe.throw(_("A Sales Manager role is required"), frappe.PermissionError)
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


def _discount_authority(user=None):
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if {"Sales Manager", "System Manager"}.intersection(roles):
		return 100.0
	return flt(frappe.db.get_value(
		"Mobile Sales Discount Authority",
		{"user": user, "enabled": 1},
		"maximum_discount_percent",
	) or 0, 3)


def _discount_context(discount_percent=None, user=None):
	requested = flt(discount_percent, 3)
	if requested < 0 or requested > 100:
		frappe.throw(_("Discount Percent must be between 0 and 100"))
	authority = _discount_authority(user)
	return {
		"discount_percent": requested,
		"discount_authority_percent": authority,
		"discount_requires_approval": bool(requested > authority + 0.0001),
	}


def _set_order_discount(doc, discount_percent):
	doc.apply_discount_on = "Grand Total"
	doc.additional_discount_percentage = flt(discount_percent, 3)
	doc.discount_amount = 0
	doc.run_method("calculate_taxes_and_totals")


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


def _make_order(
	branch,
	warehouse,
	customer,
	items,
	delivery_date=None,
	po_no=None,
	remarks=None,
	delivery_location=None,
	discount_percent=None,
	discount_reason=None,
):
	context = _sales_context(branch, warehouse)
	customer_doc = _customer_doc(customer)
	price_list = _customer_price_list(customer_doc, context.price_list)
	discount = _discount_context(discount_percent)
	if discount["discount_requires_approval"] and not str(discount_reason or "").strip():
		frappe.throw(_("A reason is required when requesting discount approval"))
	delivery_date = delivery_date or add_days(nowdate(), 1)
	delivery_rule = _delivery_location_rule(customer_doc.name, delivery_location)
	_validate_delivery_date(delivery_rule, delivery_date)
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
	if delivery_rule:
		values["shipping_address_name"] = delivery_rule["address_name"]
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
	discount["original_grand_total"] = flt(doc.grand_total, 2)
	_set_order_discount(doc, discount["discount_percent"])
	return doc, context, _credit_context(customer_doc.name, context.company), delivery_rule, discount


def _approval_for_order(order):
	if not order:
		return None
	return frappe.db.get_value(
		"Mobile Sales Discount Approval",
		{"sales_order": order},
		["name", "status", "requested_by", "requested_percent", "authority_percent", "reason", "requested_at", "decided_by", "decided_at", "decision_comment"],
		as_dict=True,
	)


def _order_response(doc, credit=None, duplicate=False, delivery_rule=None, discount=None):
	credit = credit or _credit_context(doc.customer, doc.company)
	approval = None if doc.is_new() else _approval_for_order(doc.name)
	discount = discount or _discount_context(doc.get("additional_discount_percentage"))
	if delivery_rule is None and doc.get("shipping_address_name"):
		location_name = frappe.db.get_value(
			"Mobile Sales Delivery Location",
			{"customer": doc.customer, "address": doc.shipping_address_name, "enabled": 1},
			"name",
		)
		if location_name:
			delivery_rule = _delivery_location_rule(doc.customer, location_name)
	minimum_order_value = flt(delivery_rule.get("minimum_order_value"), 2) if delivery_rule else 0
	delivery_warning = (
		_("Order is {0} below the minimum order value for {1}").format(
			flt(minimum_order_value - flt(doc.grand_total), 2),
			delivery_rule["location_name"],
		)
		if minimum_order_value and flt(doc.grand_total) < minimum_order_value
		else None
	)
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
		"delivery_location": delivery_rule["name"] if delivery_rule else None,
		"delivery_location_name": delivery_rule["location_name"] if delivery_rule else None,
		"shipping_address_name": doc.get("shipping_address_name"),
		"delivery_instructions": delivery_rule.get("instructions") if delivery_rule else None,
		"minimum_order_value": minimum_order_value,
		"delivery_warning": delivery_warning,
		"discount_percent": flt(doc.get("additional_discount_percentage"), 3),
		"discount_authority_percent": discount["discount_authority_percent"],
		"discount_requires_approval": bool(approval and approval.status == "Pending") or discount["discount_requires_approval"],
		"approval_status": approval.status if approval else None,
		"approval_reason": approval.reason if approval else None,
		"approval_comment": approval.decision_comment if approval else None,
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
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"roles": frappe.get_roles(user),
		"discount_authority_percent": _discount_authority(user),
		"branches": branches,
		"warehouses": warehouses,
		**context,
	}


@frappe.whitelist()
def validate_discount(customer, discount_percent, order_total=None):
	_require_sales_user()
	_customer_doc(customer)
	return {**_discount_context(discount_percent), "order_total": flt(order_total, 2)}


def _discount_manager_users():
	users = set()
	for role in ("Sales Manager", "System Manager"):
		users.update(frappe.get_all("Has Role", filters={"role": role}, pluck="parent", limit_page_length=500))
	if not users:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", list(users)], "enabled": 1, "user_type": "System User"},
		pluck="name",
		limit_page_length=500,
	)


def _create_discount_notification(for_user, subject, order, from_user):
	try:
		frappe.get_doc({
			"doctype": "Notification Log",
			"subject": subject,
			"email_content": subject,
			"for_user": for_user,
			"from_user": from_user,
			"type": "Alert",
			"document_type": "Sales Order",
			"document_name": order,
		}).insert(ignore_permissions=True)
		frappe.publish_realtime(
			"mobile_sales_discount_approval",
			{"sales_order": order, "subject": subject},
			user=for_user,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Mobile Sales discount notification failed")


def _notify_discount_managers(order, percent, requested_by):
	subject = _("{0} requested {1}% discount on {2}").format(requested_by, flt(percent, 3), order)
	for user in _discount_manager_users():
		if user != requested_by:
			_create_discount_notification(user, subject, order, requested_by)


def _sync_discount_approval(doc, discount, reason=None):
	existing = _approval_for_order(doc.name)
	if not discount["discount_requires_approval"]:
		if existing and existing.status != "Withdrawn":
			frappe.db.set_value(
				"Mobile Sales Discount Approval",
				existing.name,
				{"status": "Withdrawn", "decided_by": frappe.session.user, "decided_at": now_datetime(), "decision_comment": _("Discount reduced within user authority")},
			)
		return _approval_for_order(doc.name)
	reason = str(reason or "").strip()
	if not reason:
		frappe.throw(_("A reason is required when requesting discount approval"))
	requested = discount["discount_percent"]
	if existing and existing.status == "Approved" and abs(flt(existing.requested_percent) - requested) < 0.0001:
		return existing
	values = {
		"status": "Pending",
		"requested_by": frappe.session.user,
		"requested_percent": requested,
		"authority_percent": discount["discount_authority_percent"],
		"reason": reason,
		"requested_at": now_datetime(),
		"original_grand_total": flt(discount.get("original_grand_total") or flt(doc.grand_total) + flt(doc.discount_amount), 2),
		"requested_grand_total": flt(doc.grand_total, 2),
		"decided_by": None,
		"decided_at": None,
		"decision_comment": None,
	}
	if existing:
		frappe.db.set_value("Mobile Sales Discount Approval", existing.name, values)
	else:
		frappe.get_doc({
			"doctype": "Mobile Sales Discount Approval",
			"sales_order": doc.name,
			**values,
		}).insert(ignore_permissions=True)
	_notify_discount_managers(doc.name, requested, frappe.session.user)
	return _approval_for_order(doc.name)


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


def _customer_delivery_locations(customer):
	"""Enabled, Customer-linked delivery rules with no unrelated Address leakage."""
	rows = frappe.get_all(
		"Mobile Sales Delivery Location",
		filters={"customer": customer, "enabled": 1},
		fields=["name", "location_name", "address", "is_default", "instructions", "minimum_order_value", *_DELIVERY_DAY_FIELDS],
		order_by="is_default desc, location_name, name",
		limit_page_length=100,
	)
	if not rows:
		return []
	address_names = [row.address for row in rows if row.address]
	linked = set(frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Address",
			"parent": ["in", address_names],
			"link_doctype": "Customer",
			"link_name": customer,
		},
		pluck="parent",
		limit_page_length=max(len(address_names), 1),
	))
	addresses = {
		row.name: row
		for row in frappe.get_all(
			"Address",
			filters={"name": ["in", list(linked) or [""]], "disabled": 0},
			fields=["name", "address_title", "address_line1", "address_line2", "city", "county", "state", "pincode", "country", "phone", "email_id"],
			limit_page_length=max(len(linked), 1),
		)
	}
	result = []
	for row in rows:
		address = addresses.get(row.address)
		if not address:
			continue
		address_display = ", ".join(
			str(value).strip()
			for value in (
				address.address_line1,
				address.address_line2,
				address.city,
				address.county,
				address.state,
				address.pincode,
				address.country,
			)
			if value
		)
		result.append({
			"name": row.name,
			"location_name": row.location_name or address.address_title or row.address,
			"address_name": row.address,
			"address": address_display,
			"phone": address.phone,
			"email_id": address.email_id,
			"is_default": bool(row.is_default),
			"delivery_days": [field.title() for field in _DELIVERY_DAY_FIELDS if cint(row.get(field))],
			"instructions": row.instructions,
			"minimum_order_value": flt(row.minimum_order_value, 2),
		})
	return result


def _delivery_location_rule(customer, delivery_location=None):
	locations = _customer_delivery_locations(customer)
	if not locations:
		if delivery_location:
			frappe.throw(_("Delivery location {0} is not available for customer {1}").format(delivery_location, customer))
		return None
	if delivery_location:
		for location in locations:
			if location["name"] == delivery_location:
				return location
		frappe.throw(_("Delivery location {0} is not available for customer {1}").format(delivery_location, customer))
	return next((location for location in locations if location["is_default"]), locations[0])


def _validate_delivery_date(rule, delivery_date):
	if not rule or not rule["delivery_days"]:
		return
	weekday = getdate(delivery_date).strftime("%A")
	if weekday not in rule["delivery_days"]:
		frappe.throw(
			_("{0} does not deliver on {1}. Available days: {2}").format(
				rule["location_name"],
				weekday,
				", ".join(rule["delivery_days"]),
			)
		)


@frappe.whitelist()
def get_customer_delivery_locations(customer):
	_require_sales_user()
	customer = _customer_doc(customer).name
	return {"locations": _customer_delivery_locations(customer)}


def _customer_assortment_rules(customer):
	"""Explicit allow-list rules; no configured rows means the full permitted catalogue."""
	rows = frappe.get_all(
		"Mobile Sales Assortment",
		filters={"customer": customer, "enabled": 1},
		fields=["item", "item_group"],
		order_by="modified desc",
		limit_page_length=5000,
	)
	item_groups = sorted({row.item_group for row in rows if row.item_group})
	return {
		"configured": bool(rows),
		"items": sorted({row.item for row in rows if row.item}),
		"item_groups": item_groups,
		"expanded_item_groups": _expanded_item_groups(item_groups),
	}


def _expanded_item_groups(item_groups):
	result = set()
	for item_group in item_groups:
		bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
		if not bounds:
			continue
		result.update(frappe.get_all(
			"Item Group",
			filters={"lft": [">=", bounds.lft], "rgt": ["<=", bounds.rgt]},
			pluck="name",
			limit_page_length=5000,
		))
	return sorted(result)


def _assortment_item_codes(rules):
	"""Resolve explicit items plus every descendant of configured Item Groups."""
	if not rules.get("configured"):
		return None
	or_filters = {}
	if rules.get("items"):
		or_filters["name"] = ["in", rules["items"]]
	groups = rules.get("expanded_item_groups") or _expanded_item_groups(rules.get("item_groups") or [])
	if groups:
		or_filters["item_group"] = ["in", groups]
	if not or_filters:
		return []
	return frappe.get_list(
		"Item",
		filters={"disabled": 0, "is_sales_item": 1},
		or_filters=or_filters,
		pluck="name",
		limit_page_length=5000,
	)


@frappe.whitelist()
def get_customer_assortment(customer):
	_require_sales_user()
	customer = _customer_doc(customer).name
	return _customer_assortment_rules(customer)


def _customer_item_history_by_item(customer, item_codes, months=3):
	"""Comparable stock-UOM order history from permitted submitted Sales Orders."""
	item_codes = list(dict.fromkeys(item_codes or []))[:_MAX_PAGE_LENGTH]
	months = min(max(cint(months) or 3, 1), 12)
	if not customer or not item_codes:
		return {}
	orders = frappe.get_list(
		"Sales Order",
		filters={
			"customer": customer,
			"docstatus": 1,
			"status": ["!=", "Closed"],
			"transaction_date": [">=", add_months(nowdate(), -months)],
		},
		fields=["name", "transaction_date"],
		order_by="transaction_date desc, modified desc",
		page_length=500,
	)
	if not orders:
		return {}
	order_dates = {row.name: row.transaction_date for row in orders}
	lines = frappe.get_all(
		"Sales Order Item",
		filters={"parenttype": "Sales Order", "parent": ["in", list(order_dates)], "item_code": ["in", item_codes]},
		fields=["parent", "item_code", "stock_qty", "stock_uom"],
		limit_page_length=5000,
	)
	quantities_by_item = {}
	stock_uom_by_item = {}
	for line in lines:
		stock_uom_by_item.setdefault(line.item_code, line.stock_uom)
		by_order = quantities_by_item.setdefault(line.item_code, {})
		by_order[line.parent] = by_order.get(line.parent, 0) + flt(line.stock_qty)
	result = {}
	for item_code, by_order in quantities_by_item.items():
		entries = sorted(
			((order_dates[parent], quantity) for parent, quantity in by_order.items()),
			key=lambda entry: entry[0],
			reverse=True,
		)
		# A single purchase is not a reliable "usual" quantity suggestion.
		if len(entries) < 2:
			continue
		last_qty = entries[0][1]
		previous_qty = entries[1][1]
		trend = "flat" if abs(last_qty - previous_qty) < 0.001 else ("up" if last_qty > previous_qty else "down")
		average = sum(quantity for _date, quantity in entries) / len(entries)
		result[item_code] = {
			"stock_uom": stock_uom_by_item.get(item_code),
			"last_stock_qty": flt(last_qty, 3),
			"avg_stock_qty": flt(average, 3),
			"frequency_per_month": flt(len(entries) / months, 2),
			"order_count": len(entries),
			"trend": trend,
			"last_order_date": str(entries[0][0]),
		}
	return result


@frappe.whitelist()
def get_customer_item_history(customer, item_code, months=3):
	_require_sales_user()
	customer = _customer_doc(customer).name
	return {"history": _customer_item_history_by_item(customer, [item_code], months).get(item_code)}


@frappe.whitelist()
def search_items(branch=None, warehouse=None, customer=None, search=None, barcode=None, assortment_only=0, offset=0, limit=30):
	_require_sales_user()
	context = _sales_context(branch, warehouse)
	customer_doc = _customer_doc(customer) if customer else None
	price_list = _customer_price_list(customer_doc, context.price_list) if customer_doc else context.price_list
	if barcode:
		codes = frappe.get_list("Item Barcode", filters={"barcode": barcode}, pluck="parent", limit_page_length=10)
		filters = {"name": ["in", codes or [""]], "disabled": 0, "is_sales_item": 1}
	else:
		filters = {"disabled": 0, "is_sales_item": 1}
	if cint(assortment_only) and customer_doc:
		rules = _customer_assortment_rules(customer_doc.name)
		assortment_codes = _assortment_item_codes(rules)
		if assortment_codes is not None:
			if "name" in filters:
				barcode_codes = set(filters["name"][1])
				assortment_codes = [code for code in assortment_codes if code in barcode_codes]
			filters["name"] = ["in", assortment_codes or [""]]
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
	item_codes = [row.name for row in rows]
	stock_by_item, rate_by_item, uom_by_item = _item_stock_and_rates(item_codes, context.warehouse, price_list)
	history_by_item = _customer_item_history_by_item(customer_doc.name, item_codes) if customer_doc else {}
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
			"customer_history": history_by_item.get(row.name),
		})
	return {"items": rows, "price_list": price_list, "warehouse": context.warehouse, "currency": context.currency}


@frappe.whitelist()
def preview_order(customer, items, branch=None, warehouse=None, delivery_date=None, po_no=None, remarks=None, delivery_location=None, discount_percent=None, discount_reason=None):
	_require_sales_user()
	doc, _context, credit, delivery_rule, discount = _make_order(
		branch,
		warehouse,
		customer,
		items,
		delivery_date,
		po_no,
		remarks,
		delivery_location,
		discount_percent,
		discount_reason,
	)
	return _order_response(doc, credit, delivery_rule=delivery_rule, discount=discount)


@frappe.whitelist()
def create_order(request_id, customer, items, branch=None, warehouse=None, delivery_date=None, po_no=None, remarks=None, delivery_location=None, discount_percent=None, discount_reason=None):
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
	doc, _context, credit, delivery_rule, discount = _make_order(
		branch,
		warehouse,
		customer,
		items,
		delivery_date,
		po_no,
		remarks,
		delivery_location,
		discount_percent,
		discount_reason,
	)
	doc.insert()
	_sync_discount_approval(doc, discount, discount_reason)
	frappe.db.set_value("Mobile Sales Order Request", request_id, {"sales_order": doc.name, "status": "Created"})
	return _order_response(doc, credit, delivery_rule=delivery_rule, discount=discount)


@frappe.whitelist()
def update_order(order, items, delivery_date=None, po_no=None, remarks=None, delivery_location=None, discount_percent=None, discount_reason=None):
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

	existing_approval = _approval_for_order(doc.name)
	requested_discount = doc.additional_discount_percentage if discount_percent is None else discount_percent
	discount = _discount_context(requested_discount)
	effective_reason = discount_reason or (existing_approval.reason if existing_approval else None)
	if discount["discount_requires_approval"] and not str(effective_reason or "").strip():
		frappe.throw(_("A reason is required when requesting discount approval"))
	warehouse = doc.set_warehouse
	if not delivery_location and doc.get("shipping_address_name"):
		delivery_location = frappe.db.get_value(
			"Mobile Sales Delivery Location",
			{"customer": doc.customer, "address": doc.shipping_address_name, "enabled": 1},
			"name",
		)
	delivery_rule = _delivery_location_rule(doc.customer, delivery_location)
	_validate_delivery_date(delivery_rule, delivery_date or doc.delivery_date)
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
	if delivery_rule:
		doc.shipping_address_name = delivery_rule["address_name"]
	if po_no is not None:
		doc.po_no = po_no
	if remarks is not None:
		doc.remarks = remarks
	doc.run_method("set_missing_values")
	doc.run_method("calculate_taxes_and_totals")
	discount["original_grand_total"] = flt(doc.grand_total + doc.discount_amount, 2)
	_set_order_discount(doc, discount["discount_percent"])
	doc.save()
	_sync_discount_approval(doc, discount, effective_reason)
	return _order_response(doc, delivery_rule=delivery_rule, discount=discount)


def _reorder_summary(doc):
	"""Return only the prior-order fields needed to seed a new local draft."""
	return {
		"order_name": doc.name,
		"customer": doc.customer,
		"customer_name": doc.customer_name,
		"transaction_date": str(doc.transaction_date),
		"currency": doc.currency,
		"grand_total": flt(doc.grand_total, 2),
		"item_count": len(doc.items),
		"items": [
			{"item_code": row.item_code, "item_name": row.item_name, "uom": row.uom, "qty": flt(row.qty)}
			for row in doc.items
		],
	}


def _submitted_order_rows(customer=None, limit=3):
	filters = {"docstatus": 1, "status": ["not in", ["Cancelled", "Closed"]]}
	if customer:
		filters["customer"] = customer
	return frappe.get_list(
		"Sales Order",
		filters=filters,
		fields=["name", "customer"],
		order_by="transaction_date desc, modified desc",
		page_length=min(max(cint(limit) or 3, 1), 30),
	)


@frappe.whitelist()
def get_recent_reorder_candidates(limit=3):
	"""Recent submitted orders for distinct permitted customers (maximum ten)."""
	_require_sales_user()
	limit = min(max(cint(limit) or 3, 1), 10)
	orders = []
	seen_customers = set()
	# Read a bounded window because several recent orders may belong to one customer.
	for row in _submitted_order_rows(limit=limit * 3):
		if row.customer in seen_customers:
			continue
		doc = frappe.get_doc("Sales Order", row.name)
		doc.check_permission("read")
		orders.append(_reorder_summary(doc))
		seen_customers.add(row.customer)
		if len(orders) >= limit:
			break
	return {"orders": orders}


@frappe.whitelist()
def get_customer_last_order(customer):
	"""Last submitted order for one permitted customer, or null when none exists."""
	_require_sales_user()
	customer = _customer_doc(customer).name
	rows = _submitted_order_rows(customer=customer, limit=1)
	if not rows:
		return {"order": None}
	doc = frappe.get_doc("Sales Order", rows[0].name)
	doc.check_permission("read")
	return {"order": _reorder_summary(doc)}


@frappe.whitelist()
def request_discount_approval(order_name, discount_percent, reason):
	_require_sales_user()
	doc = frappe.get_doc("Sales Order", order_name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a Draft Sales Order can request discount approval"))
	discount = _discount_context(discount_percent)
	if not discount["discount_requires_approval"]:
		frappe.throw(_("This discount is already within your authority"))
	discount["original_grand_total"] = flt(doc.grand_total + doc.discount_amount, 2)
	_set_order_discount(doc, discount["discount_percent"])
	doc.save()
	approval = _sync_discount_approval(doc, discount, reason)
	return {"approval": approval, "order": _order_response(doc, discount=discount)}


@frappe.whitelist()
def get_discount_approvals(status="Pending", limit=50):
	_require_sales_manager()
	filters = {"status": status} if status else {}
	approvals = frappe.get_all(
		"Mobile Sales Discount Approval",
		filters=filters,
		fields=["name", "sales_order", "status", "requested_by", "requested_percent", "authority_percent", "reason", "requested_at", "original_grand_total", "requested_grand_total", "decided_by", "decided_at", "decision_comment"],
		order_by="requested_at desc",
		limit_page_length=min(max(cint(limit) or 50, 1), 100),
	)
	if not approvals:
		return {"approvals": []}
	orders = {
		row.name: row
		for row in frappe.get_list(
			"Sales Order",
			filters={"name": ["in", [approval.sales_order for approval in approvals]]},
			fields=["name", "customer", "customer_name", "company", "currency", "grand_total", "status", "docstatus", "modified"],
			page_length=len(approvals),
		)
	}
	result = []
	for approval in approvals:
		order = orders.get(approval.sales_order)
		if not order:
			continue
		approval.update({
			"customer": order.customer,
			"customer_name": order.customer_name,
			"company": order.company,
			"currency": order.currency,
			"grand_total": flt(order.grand_total, 2),
			"order_status": order.status,
			"docstatus": order.docstatus,
		})
		result.append(approval)
	return {"approvals": result}


@frappe.whitelist()
def approve_discount(order_name, approved, comment=None):
	manager = _require_sales_manager()
	approval = frappe.db.get_value(
		"Mobile Sales Discount Approval",
		{"sales_order": order_name},
		["name", "status", "requested_by", "requested_percent"],
		as_dict=True,
	)
	if not approval or approval.status != "Pending":
		frappe.throw(_("There is no pending discount approval for {0}").format(order_name))
	is_approved = bool(cint(approved))
	comment = str(comment or "").strip()
	if not is_approved and not comment:
		frappe.throw(_("A comment is required when rejecting a discount"))
	doc = frappe.get_doc("Sales Order", order_name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a Draft Sales Order discount can be decided"))
	_set_order_discount(doc, approval.requested_percent if is_approved else 0)
	doc.save()
	status = "Approved" if is_approved else "Rejected"
	frappe.db.set_value(
		"Mobile Sales Discount Approval",
		approval.name,
		{"status": status, "decided_by": manager, "decided_at": now_datetime(), "decision_comment": comment},
	)
	subject = _("{0} discount request for {1}").format(status, order_name)
	_create_discount_notification(approval.requested_by, subject, order_name, manager)
	return {"approval": _approval_for_order(order_name), "order": _order_response(doc)}


@frappe.whitelist()
def get_orders(customer=None, offset=0, limit=20):
	_require_sales_user()
	filters = {"customer": customer} if customer else {}
	orders = frappe.get_list(
		"Sales Order",
		filters=filters,
		fields=["name", "customer", "customer_name", "transaction_date", "delivery_date", "status", "currency", "grand_total", "additional_discount_percentage", "branch", "set_warehouse", "modified"],
		start=cint(offset),
		page_length=_page_length(limit),
		order_by="modified desc",
	)
	if orders:
		approvals = {
			row.sales_order: row
			for row in frappe.get_all(
				"Mobile Sales Discount Approval",
				filters={"sales_order": ["in", [order.name for order in orders]]},
				fields=["sales_order", "status", "requested_percent", "reason", "decision_comment"],
				limit_page_length=len(orders),
			)
		}
		for order in orders:
			approval = approvals.get(order.name)
			order.update({
				"approval_status": approval.status if approval else None,
				"approval_reason": approval.reason if approval else None,
				"approval_comment": approval.decision_comment if approval else None,
				"requested_discount_percent": flt(approval.requested_percent, 3) if approval else None,
			})
	return {"orders": orders}


@frappe.whitelist()
def get_order(order):
	_require_sales_user()
	doc = frappe.get_doc("Sales Order", order)
	doc.check_permission("read")
	return _order_response(doc)
