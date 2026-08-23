import base64
import hashlib
import hmac
import json
import secrets
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	flt,
	get_datetime,
	now_datetime,
	nowdate,
	strip_html_tags,
)

from aimatic.branch_management.utils import get_branch_defaults

_OAUTH_APP = "Aimatic Shopping"
_DELIVERY_METHOD = "Store Pickup"
_PAYMENT_METHOD = "Cash on Delivery"
_QUOTE_MINUTES = 10


def _settings(require_enabled=True):
	doc = frappe.get_single("Shopping Settings")
	if require_enabled and not cint(doc.enabled):
		frappe.throw(_("Online shopping is not enabled"))
	if not doc.default_branch:
		frappe.throw(_("Shopping Settings requires a Default Branch"))
	branch = frappe.get_cached_doc("Branch", doc.default_branch)
	warehouse = get_branch_defaults(branch.name).get("finished_goods_warehouse")
	if not warehouse:
		frappe.throw(_("The shopping Branch has no Finished Goods Warehouse"))
	price_list = (
		doc.price_list
		or branch.get("default_selling_price_list")
		or frappe.db.get_single_value("Selling Settings", "selling_price_list")
	)
	if not price_list:
		frappe.throw(_("Shopping has no selling Price List"))
	return doc, frappe._dict(
		branch=branch.name,
		company=branch.company,
		warehouse=warehouse,
		price_list=price_list,
		currency=frappe.db.get_value("Price List", price_list, "currency")
		or frappe.db.get_value("Company", branch.company, "default_currency"),
	)


def _customer_for_user():
	user = frappe.session.user
	if user in (None, "", "Guest"):
		frappe.throw(_("Customer sign-in required"), frappe.AuthenticationError)
	if user != "Administrator" and frappe.db.get_value("User", user, "user_type") != "Website User":
		frappe.throw(_("Shopping is available only to customer accounts"), frappe.PermissionError)
	customer = frappe.db.get_value(
		"Portal User", {"user": user, "parenttype": "Customer", "parentfield": "portal_users"}, "parent"
	)
	if not customer:
		frappe.throw(
			_("This account is not linked to a Customer. Ask store support to link it."),
			frappe.PermissionError,
		)
	if cint(frappe.db.get_value("Customer", customer, "disabled")):
		frappe.throw(_("This customer account is disabled"), frappe.PermissionError)
	return user, customer


def _website_user():
	user = frappe.session.user
	if user in (None, "", "Guest"):
		frappe.throw(_("Customer sign-in required"), frappe.AuthenticationError)
	if frappe.db.get_value("User", user, "user_type") != "Website User":
		frappe.throw(_("Shopping registration is available only to Website Users"), frappe.PermissionError)
	return user


def _product_rows(search=None, category=None, brand=None, item_code=None, offset=0, limit=30):
	filters = {"enabled": 1}
	if category:
		filters["category"] = category
	if item_code:
		filters["item"] = item_code
	rows = frappe.get_all(
		"Shopping Product",
		filters=filters,
		fields=[
			"item",
			"category",
			"featured",
			"public_name",
			"public_description",
			"image",
			"display_order",
		],
		order_by="display_order asc, modified desc",
		limit_start=cint(offset),
		limit_page_length=min(max(cint(limit) or 30, 1), 60),
	)
	items = {
		row.name: row
		for row in frappe.get_all(
			"Item",
			filters={"name": ["in", [x.item for x in rows] or [""]], "disabled": 0, "is_sales_item": 1},
			fields=["name", "item_name", "description", "brand", "stock_uom", "image"],
		)
	}
	result = []
	for product in rows:
		item = items.get(product.item)
		if not item or (brand and item.brand != brand):
			continue
		name = product.public_name or item.item_name
		if (
			search
			and search.lower() not in f"{product.item} {name} {product.category} {item.brand or ''}".lower()
		):
			continue
		result.append((product, item))
	return result


def _price_and_stock(item_code, context, price_list=None):
	price_list = price_list or context.price_list
	rate = frappe.db.get_value(
		"Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}, "price_list_rate"
	)
	bin_row = (
		frappe.db.get_value(
			"Bin",
			{"item_code": item_code, "warehouse": context.warehouse},
			["actual_qty", "reserved_qty"],
			as_dict=True,
		)
		or {}
	)
	available = max(flt(bin_row.get("actual_qty")) - flt(bin_row.get("reserved_qty")), 0)
	return flt(rate, 2), available


def _public_product(product, item, context, price_list=None):
	rate, available = _price_and_stock(item.name, context, price_list)
	return {
		"item_code": item.name,
		"item_name": product.public_name or item.item_name,
		"description": product.public_description or item.description or "",
		"image_url": product.image or item.image or "",
		"category": product.category,
		"brand": item.brand or "",
		"uom": item.stock_uom,
		"rate": rate,
		"currency": context.currency,
		"available": available > 0,
		"available_qty": available,
		"featured": bool(product.featured),
	}


def _customer_price_list(customer, fallback):
	group, direct = frappe.db.get_value("Customer", customer, ["customer_group", "default_price_list"])
	return direct or frappe.db.get_value("Customer Group", group, "default_price_list") or fallback


def _parse_items(items):
	if isinstance(items, str):
		items = json.loads(items)
	if not isinstance(items, list) or not items:
		frappe.throw(_("Your cart is empty"))
	result = []
	for row in items:
		code = row.get("item_code") if isinstance(row, dict) else None
		qty = flt(row.get("qty")) if isinstance(row, dict) else 0
		line_id = row.get("line_id") if isinstance(row, dict) else None
		if not code or qty <= 0:
			frappe.throw(_("Every cart line needs an item and quantity"))
		if not frappe.db.exists("Shopping Product", {"item": code, "enabled": 1}):
			frappe.throw(_("Item {0} is not available for online shopping").format(code))
		result.append({"line_id": line_id or code, "item_code": code, "qty": qty, "uom": row.get("uom")})
	return result


def _make_order(customer, items):
	settings, context = _settings()
	price_list = _customer_price_list(customer, context.price_list)
	delivery_date = add_days(nowdate(), 1)
	doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": customer,
			"company": context.company,
			"branch": context.branch,
			"set_warehouse": context.warehouse,
			"selling_price_list": price_list,
			"delivery_date": delivery_date,
			"order_type": "Sales",
			"remarks": f"Ai Matic Shopping – {_DELIVERY_METHOD} / {_PAYMENT_METHOD}\n{settings.pickup_instructions or ''}",
		}
	)
	# The shopping endpoint has already bound the Website User to its Customer.
	# ERPNext must be able to read that Customer while populating the draft, but
	# its normal Sales Order validation and document hooks still run below.
	doc.flags.ignore_permissions = True
	parsed = _parse_items(items)
	for item in parsed:
		_rate, available = _price_and_stock(item["item_code"], context, price_list)
		if available + 0.0001 < item["qty"]:
			frappe.throw(_("Requested quantity for {0} is no longer available").format(item["item_code"]))
		doc.append(
			"items",
			{
				"item_code": item["item_code"],
				"qty": item["qty"],
				"uom": item["uom"],
				"warehouse": context.warehouse,
				"delivery_date": delivery_date,
			},
		)
	doc.run_method("set_missing_values")
	doc.run_method("calculate_taxes_and_totals")
	return doc, context, parsed


def _signing_key():
	return (frappe.get_conf().encryption_key or frappe.get_conf().secret_key).encode()


def _quote_token(payload):
	raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
	signature = hmac.new(_signing_key(), raw, hashlib.sha256).digest()
	return f"{base64.urlsafe_b64encode(raw).decode().rstrip('=')}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


def _read_quote(token):
	try:
		encoded, signed = token.split(".", 1)
		raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
		signature = base64.urlsafe_b64decode(signed + "=" * (-len(signed) % 4))
		if not hmac.compare_digest(signature, hmac.new(_signing_key(), raw, hashlib.sha256).digest()):
			raise ValueError
		payload = json.loads(raw)
	except (ValueError, TypeError, json.JSONDecodeError):
		frappe.throw(_("Checkout quote is invalid. Refresh your cart."), frappe.PermissionError)
	if now_datetime() >= get_datetime(payload["expires_at"]):
		frappe.throw(_("Checkout quote expired. Refresh your cart."))
	return payload


@frappe.whitelist(allow_guest=True)
def get_public_config(platform="capacitor", origin=None):
	client = frappe.db.get_value(
		"OAuth Client", {"app_name": _OAUTH_APP}, ["name", "default_redirect_uri"], as_dict=True
	)
	if not client:
		frappe.throw(_("Ai Matic Shopping OAuth is not configured"))
	settings = frappe.get_single("Shopping Settings")
	redirect_uri = client.default_redirect_uri
	if platform == "web":
		redirect_uri = (settings.web_redirect_uri or "").strip()
		if not redirect_uri:
			frappe.throw(_("The browser shopping callback is not configured"))
		configured_origin = "{0.scheme}://{0.netloc}".format(urlparse(redirect_uri))
		if not origin or origin.rstrip("/") != configured_origin:
			frappe.throw(_("This storefront origin is not allowed"), frappe.PermissionError)
	return {
		"oauth_client_id": client.name,
		"redirect_uri": redirect_uri,
		"scope": "shopping-customer",
		"signup_url": f"{frappe.utils.get_url()}/login#signup",
		"allow_self_registration": bool(cint(settings.allow_self_registration)),
	}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def get_storefront(branch=None):
	settings, context = _settings()
	categories = frappe.get_all("Shopping Product", filters={"enabled": 1}, distinct=True, pluck="category")
	return {
		"store_name": settings.store_name,
		"branch": context.branch,
		"currency": context.currency,
		"categories": [{"name": value, "label": value} for value in categories],
		"delivery_methods": [
			{
				"name": _DELIVERY_METHOD,
				"label": _DELIVERY_METHOD,
				"instructions": settings.pickup_instructions or "",
			}
		],
		"payment_methods": [{"name": _PAYMENT_METHOD, "label": _PAYMENT_METHOD}],
	}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def search_products(branch=None, category=None, brand=None, search=None, offset=0, limit=30):
	_settings_doc, context = _settings()
	return {
		"products": [
			_public_product(product, item, context)
			for product, item in _product_rows(search, category, brand, offset=offset, limit=limit)
		]
	}


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60)
def get_product(item_code, branch=None):
	_settings_doc, context = _settings()
	rows = _product_rows(item_code=item_code, limit=1)
	if not rows:
		frappe.throw(_("Product not found"), frappe.DoesNotExistError)
	return _public_product(rows[0][0], rows[0][1], context)


@frappe.whitelist()
def get_customer_account():
	user, customer = _customer_for_user()
	addresses = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
		pluck="parent",
	)
	return {
		"user": user,
		"customer": customer,
		"customer_name": frappe.db.get_value("Customer", customer, "customer_name"),
		"addresses": [
			{"name": name, "label": frappe.db.get_value("Address", name, "address_title") or name}
			for name in addresses
		],
	}


@frappe.whitelist()
@rate_limit(limit=5, seconds=3600)
def register_customer(customer_name, mobile_no=None):
	user = _website_user()
	settings = frappe.get_single("Shopping Settings")
	if not cint(settings.enabled) or not cint(settings.allow_self_registration):
		frappe.throw(_("Customer self-registration is not enabled"), frappe.PermissionError)
	if not settings.registration_customer_group or not settings.registration_territory:
		frappe.throw(_("Customer registration is not configured"))

	# Serialize registration for this User. Retrying returns the created Customer;
	# an existing unrelated Customer is never claimed by email, mobile, or name.
	frappe.db.sql("select name from `tabUser` where name=%s for update", user)
	existing = frappe.db.get_value(
		"Portal User", {"user": user, "parenttype": "Customer", "parentfield": "portal_users"}, "parent"
	)
	if existing:
		return {
			"customer": existing,
			"customer_name": frappe.db.get_value("Customer", existing, "customer_name"),
			"created": False,
		}

	clean_name = strip_html_tags((customer_name or "").strip())[:140]
	clean_mobile = strip_html_tags((mobile_no or "").strip())[:30]
	if len(clean_name) < 2:
		frappe.throw(_("Customer name must contain at least two characters"))

	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": clean_name,
			"customer_type": "Individual",
			"customer_group": settings.registration_customer_group,
			"territory": settings.registration_territory,
			"email_id": user,
			"mobile_no": clean_mobile or None,
			"portal_users": [{"user": user}],
		}
	)
	customer.insert(ignore_permissions=True)
	return {"customer": customer.name, "customer_name": customer.customer_name, "created": True}


@frappe.whitelist()
def get_addresses():
	return {"addresses": get_customer_account()["addresses"]}


@frappe.whitelist()
@rate_limit(limit=3, seconds=3600)
def request_account_deletion():
	"""Logs a customer-initiated account/data deletion request for admin
	follow-up - this does NOT delete or anonymize anything itself. Real
	Customer records are linked to Sales Orders/Invoices that may need to be
	retained for tax/accounting record-keeping, so the actual purge is a
	reviewed admin action, not an automatic one. Satisfies the Play Store
	requirement that an app with real user accounts provide a discoverable
	way to request deletion; see aimatic.tech/data-deletion.html for the
	public-facing description of this same process.
	"""
	user, customer = _customer_for_user()

	existing = frappe.db.get_value(
		"Shopping Account Deletion Request",
		{"customer": customer, "status": ["in", ["Requested", "In Review"]]},
		"name",
	)
	if existing:
		return {
			"name": existing,
			"status": frappe.db.get_value("Shopping Account Deletion Request", existing, "status"),
			"already_requested": True,
		}

	customer_doc = frappe.db.get_value("Customer", customer, ["email_id", "mobile_no"], as_dict=True) or {}
	request = frappe.get_doc(
		{
			"doctype": "Shopping Account Deletion Request",
			"customer": customer,
			"user": user,
			"email": customer_doc.get("email_id") or user,
			"mobile_no": customer_doc.get("mobile_no"),
			"status": "Requested",
		}
	)
	request.insert(ignore_permissions=True)
	return {"name": request.name, "status": "Requested", "already_requested": False}


@frappe.whitelist()
def get_account_deletion_status():
	_user, customer = _customer_for_user()
	row = frappe.db.get_value(
		"Shopping Account Deletion Request",
		{"customer": customer},
		["name", "status", "creation"],
		order_by="creation desc",
		as_dict=True,
	)
	return {"request": row}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def quote_cart(branch=None, delivery_method=None, address_name=None, items=None):
	_user, customer = _customer_for_user()
	if delivery_method != _DELIVERY_METHOD:
		frappe.throw(_("Only Store Pickup is currently available"))
	doc, _context, parsed = _make_order(customer, items)
	expires = add_to_date(now_datetime(), minutes=_QUOTE_MINUTES)
	payload = {
		"nonce": secrets.token_urlsafe(12),
		"user": frappe.session.user,
		"customer": customer,
		"items": parsed,
		"grand_total": flt(doc.grand_total, 2),
		"expires_at": str(expires),
	}
	return {
		"quoteToken": _quote_token(payload),
		"currency": doc.currency,
		"subtotal": flt(doc.net_total, 2),
		"discount": flt(doc.discount_amount, 2),
		"taxes": flt(doc.total_taxes_and_charges, 2),
		"deliveryCharge": 0,
		"grandTotal": flt(doc.grand_total, 2),
		"expiresAt": str(expires),
		"lines": [
			{
				"lineId": item["line_id"],
				"itemCode": row.item_code,
				"quantity": flt(row.qty),
				"rate": flt(row.rate, 2),
				"amount": flt(row.amount, 2),
				"available": True,
			}
			for item, row in zip(parsed, doc.items)
		],
	}


@frappe.whitelist()
@rate_limit(limit=10, seconds=60)
def place_order(request_id, quote_token, address_name=None, delivery_method=None, payment_method=None):
	user, customer = _customer_for_user()
	if delivery_method != _DELIVERY_METHOD or payment_method != _PAYMENT_METHOD:
		frappe.throw(_("Checkout supports Store Pickup with Cash on Delivery only"))
	if not request_id or len(request_id) > 140:
		frappe.throw(_("A valid request_id is required"))
	quote = _read_quote(quote_token)
	if quote.get("user") != user or quote.get("customer") != customer:
		frappe.throw(_("This checkout quote belongs to another account"), frappe.PermissionError)
	existing = frappe.db.get_value(
		"Shopping Order Request", request_id, ["user", "sales_order"], as_dict=True
	)
	if existing:
		if existing.user != user:
			frappe.throw(_("This request ID belongs to another account"), frappe.PermissionError)
		if existing.sales_order:
			return {"sales_order": existing.sales_order, "duplicate": True}
		frappe.throw(_("This checkout is already being processed"))
	frappe.get_doc(
		{
			"doctype": "Shopping Order Request",
			"request_id": request_id,
			"user": user,
			"customer": customer,
			"delivery_method": delivery_method,
			"payment_method": payment_method,
			"status": "Processing",
			"created_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	doc, _context, _parsed = _make_order(customer, quote["items"])
	if flt(doc.grand_total, 2) != flt(quote["grand_total"], 2):
		frappe.throw(_("Prices changed. Refresh your cart before placing the order."))
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.db.set_value("Shopping Order Request", request_id, {"sales_order": doc.name, "status": "Created"})
	return {
		"sales_order": doc.name,
		"status": doc.status,
		"grand_total": flt(doc.grand_total, 2),
		"currency": doc.currency,
		"duplicate": False,
	}


@frappe.whitelist()
def get_orders(offset=0, limit=20):
	_user, customer = _customer_for_user()
	return {
		"orders": frappe.get_all(
			"Sales Order",
			filters={"customer": customer},
			fields=[
				"name",
				"transaction_date",
				"delivery_date",
				"status",
				"currency",
				"grand_total",
				"modified",
			],
			order_by="modified desc",
			limit_start=cint(offset),
			limit_page_length=min(max(cint(limit) or 20, 1), 50),
		)
	}


@frappe.whitelist()
def get_order_status(order):
	_user, customer = _customer_for_user()
	row = frappe.db.get_value(
		"Sales Order",
		{"name": order, "customer": customer},
		["name", "transaction_date", "delivery_date", "status", "currency", "grand_total", "modified"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Order not found"), frappe.DoesNotExistError)
	return row
