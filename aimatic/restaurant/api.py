import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, now_datetime, strip_html_tags


_OAUTH_APP = "Aimatic Restaurant Android"
_STAFF_ROLES = {"Restaurant Waiter", "Restaurant Manager", "Kitchen User", "System Manager"}
_WAITER_ROLES = {"Restaurant Waiter", "Restaurant Manager", "System Manager"}
_MANAGER_ROLES = {"Restaurant Manager", "System Manager"}
_KITCHEN_ROLES = {"Kitchen User", "Restaurant Manager", "System Manager"}
_ACTIVE_ORDER_STATUSES = ("Open", "Sent to Kitchen", "Bill Requested")
_KITCHEN_FLOW = ("Queued", "Preparing", "Ready", "Served")


def _require_roles(required=_STAFF_ROLES):
	user = frappe.session.user
	if user in (None, "", "Guest"):
		frappe.throw(_("Restaurant sign-in required"), frappe.AuthenticationError)
	roles = set(frappe.get_roles(user))
	if not roles.intersection(required):
		frappe.throw(_("You are not authorized for Restaurant operations"), frappe.PermissionError)
	if not cint(frappe.db.get_value("User", user, "enabled")):
		frappe.throw(_("This Restaurant user is disabled"), frappe.PermissionError)
	return user, roles


def _profile_is_permitted(profile, user, roles):
	if roles.intersection(_MANAGER_ROLES):
		return True
	default_branch = frappe.defaults.get_user_default("Branch", user)
	if default_branch and profile.branch and profile.branch != default_branch:
		return False
	# Kitchen users operate through the Restaurant API and may serve several
	# POS profiles at their branch. Applicable POS users remain authoritative
	# for waiters who can open and change customer-facing orders.
	if roles.intersection(_KITCHEN_ROLES):
		return True
	pos = frappe.get_cached_doc("POS Profile", profile.pos_profile)
	allowed = [row.user for row in (pos.get("applicable_for_users") or [])]
	return not allowed or user in allowed


def _permitted_profiles(branch=None):
	user, roles = _require_roles()
	filters = {"enabled": 1}
	if branch:
		filters["branch"] = branch
	rows = frappe.get_all(
		"Restaurant Profile",
		filters=filters,
		fields=["name", "profile_name", "branch", "company", "pos_profile", "default_customer", "menu_price_list", "warehouse"],
		order_by="profile_name",
		limit_page_length=200,
	)
	return [row for row in rows if _profile_is_permitted(row, user, roles)]


def _context(branch=None, restaurant_profile=None):
	profiles = _permitted_profiles(branch)
	if restaurant_profile:
		profiles = [row for row in profiles if row.name == restaurant_profile]
	if not profiles:
		frappe.throw(_("No permitted Restaurant Profile is available"), frappe.PermissionError)
	if len(profiles) > 1 and not restaurant_profile:
		frappe.throw(_("Select a Restaurant Profile"))
	profile = profiles[0]
	pos = frappe.get_cached_doc("POS Profile", profile.pos_profile)
	if cint(pos.disabled):
		frappe.throw(_("Restaurant POS Profile is disabled"))
	warehouse = profile.warehouse or pos.warehouse
	price_list = profile.menu_price_list or pos.selling_price_list
	if not warehouse or not price_list:
		frappe.throw(_("Restaurant Profile requires a Warehouse and selling Price List"))
	warehouse_row = frappe.db.get_value("Warehouse", warehouse, ["company", "disabled", "is_group"], as_dict=True)
	if not warehouse_row or warehouse_row.company != profile.company or warehouse_row.disabled or warehouse_row.is_group:
		frappe.throw(_("Restaurant Warehouse is not an active stock warehouse for this Company"))
	return frappe._dict(
		profile=profile.name,
		profile_name=profile.profile_name,
		branch=profile.branch,
		company=profile.company,
		pos_profile=profile.pos_profile,
		customer=profile.default_customer or pos.customer,
		warehouse=warehouse,
		price_list=price_list,
		currency=frappe.db.get_value("Price List", price_list, "currency")
		or frappe.db.get_value("Company", profile.company, "default_currency"),
	)


def _load_order(name, write=False):
	user, roles = _require_roles(_WAITER_ROLES)
	doc = frappe.get_doc("Restaurant Order", name)
	profiles = {row.name for row in _permitted_profiles(doc.branch)}
	if doc.restaurant_profile not in profiles:
		frappe.throw(_("You are not permitted to access this Restaurant Order"), frappe.PermissionError)
	if write and doc.waiter != user and not roles.intersection(_MANAGER_ROLES):
		frappe.throw(_("Only the assigned waiter or a Restaurant Manager may change this order"), frappe.PermissionError)
	return doc


def _lock_order(name):
	frappe.db.sql("select name from `tabRestaurant Order` where name=%s for update", name)
	return _load_order(name, write=True)


def _parse_json_list(value, label):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			frappe.throw(_("{0} must be valid JSON").format(label))
	if not isinstance(value, list):
		frappe.throw(_("{0} must be a list").format(label))
	return value


def _modifier_configuration(item_code):
	groups = frappe.get_all(
		"Restaurant Menu Modifier Group",
		filters={"parent": item_code, "parenttype": "Restaurant Menu Item", "parentfield": "modifier_groups"},
		pluck="modifier_group",
		order_by="idx",
	)
	result = []
	for name in groups:
		doc = frappe.get_cached_doc("Restaurant Modifier Group", name)
		if doc.disabled:
			continue
		result.append({
			"code": doc.name,
			"title": doc.group_name,
			"required": bool(doc.required),
			"multiple": bool(doc.allow_multiple),
			"minimum": cint(doc.minimum_selections),
			"maximum": cint(doc.maximum_selections),
			"options": [{
				"code": f"{doc.name}:{row.option_code}",
				"option_code": row.option_code,
				"label": row.label,
				"price": flt(row.price_adjustment, 2),
				"linked_item": row.linked_item,
			} for row in doc.options if not row.disabled],
		})
	return result


def _validate_modifiers(item_code, requested):
	requested = _parse_json_list(requested or [], "modifiers")
	selected = {str(row.get("code")): row for row in requested if isinstance(row, dict) and row.get("code")}
	snapshot = []
	adjustment = 0
	configuration = _modifier_configuration(item_code)
	for group in configuration:
		valid = {row["code"]: row for row in group["options"]}
		chosen = [valid[code] for code in selected if code in valid]
		minimum = max(group["minimum"], 1 if group["required"] else 0)
		if len(chosen) < minimum or len(chosen) > group["maximum"]:
			frappe.throw(_("Select the required options for {0}").format(group["title"]))
		if not group["multiple"] and len(chosen) > 1:
			frappe.throw(_("Only one option may be selected for {0}").format(group["title"]))
		for option in chosen:
			snapshot.append({"group": group["title"], **option})
			adjustment += option["price"]
	unknown = set(selected) - {row["code"] for group in configuration for row in group["options"]}
	if unknown:
		frappe.throw(_("One or more item modifiers are unavailable"))
	return snapshot, flt(adjustment, 2)


def _stock_and_prices(item_codes, context):
	bins = frappe.get_all("Bin", filters={"warehouse": context.warehouse, "item_code": ["in", item_codes or [""]]}, fields=["item_code", "actual_qty", "reserved_qty"], limit_page_length=max(len(item_codes), 1))
	prices = frappe.get_all("Item Price", filters={"price_list": context.price_list, "selling": 1, "item_code": ["in", item_codes or [""]]}, fields=["item_code", "price_list_rate"], order_by="modified desc", limit_page_length=max(len(item_codes) * 3, 100))
	stock = {row.item_code: max(flt(row.actual_qty) - flt(row.reserved_qty), 0) for row in bins}
	rates = {}
	for row in prices:
		rates.setdefault(row.item_code, flt(row.price_list_rate, 2))
	return stock, rates


def _order_response(doc):
	return {
		"name": doc.name, "branch": doc.branch, "floor": doc.floor, "table": doc.restaurant_table,
		"table_title": frappe.db.get_value("Restaurant Table", doc.restaurant_table, "title"),
		"waiter": doc.waiter, "guest_count": doc.guest_count, "status": doc.status,
		"currency": doc.currency, "net_total": flt(doc.net_total, 2), "taxes": flt(doc.total_taxes_and_charges, 2),
		"grand_total": flt(doc.grand_total, 2), "pos_invoice": doc.pos_invoice, "opened_at": doc.opened_at,
		"modified": str(doc.modified), "items": [{
			"id": row.name, "item_code": row.item, "item_name": row.item_name, "uom": row.uom,
			"quantity": flt(row.qty), "sent_quantity": flt(row.sent_qty), "rate": flt(row.rate, 2),
			"amount": flt(row.amount, 2), "notes": row.notes or "", "modifiers": json.loads(row.modifiers_json or "[]"),
			"kitchen_station": row.kitchen_station, "kitchen_status": row.kitchen_status, "added_at": row.added_at,
		} for row in doc.items],
	}


def _table_status(order):
	if not order:
		return "Available"
	if order.status == "Bill Requested":
		return "Bill requested"
	statuses = [row.kitchen_status for row in order.items]
	if any(flt(row.qty) > flt(row.sent_qty) for row in order.items):
		return "Order pending"
	if "Ready" in statuses:
		return "Needs attention"
	if "Served" in statuses and any(status != "Served" for status in statuses):
		return "Partially served"
	return "Sent to kitchen" if order.items else "Occupied"


@frappe.whitelist(allow_guest=True)
def get_public_config():
	client = frappe.db.get_value("OAuth Client", {"app_name": _OAUTH_APP}, ["name", "default_redirect_uri"], as_dict=True)
	if not client:
		frappe.throw(_("Ai Matic Restaurant OAuth is not configured"))
	return {"oauth_client_id": client.name, "redirect_uri": client.default_redirect_uri, "scope": "restaurant-waiter"}


@frappe.whitelist()
@rate_limit(limit=60, seconds=60)
def get_restaurant_bootstrap(branch=None, restaurant_profile=None):
	user, _roles = _require_roles()
	profiles = _permitted_profiles(branch)
	if not profiles:
		frappe.throw(_("No permitted Restaurant Profiles are configured"), frappe.PermissionError)
	selected = restaurant_profile or (profiles[0].name if len(profiles) == 1 else None)
	if not selected:
		return {"user": user, "full_name": frappe.utils.get_fullname(user), "profiles": profiles, "requires_profile_selection": True}
	context = _context(branch, selected)
	floor_rows = frappe.get_all("Restaurant Floor", filters={"branch": context.branch, "disabled": 0}, fields=["name", "title", "branch", "display_order"], order_by="display_order, title", limit_page_length=200)
	table_rows = frappe.get_all("Restaurant Table", filters={"branch": context.branch, "disabled": 0}, fields=["name", "title", "branch", "floor", "capacity", "display_order"], order_by="display_order, title", limit_page_length=500)
	active_names = frappe.get_all("Restaurant Order", filters={"branch": context.branch, "status": ["in", _ACTIVE_ORDER_STATUSES]}, pluck="name", limit_page_length=500)
	active = {doc.restaurant_table: doc for doc in (frappe.get_doc("Restaurant Order", name) for name in active_names)}
	menu_rows = frappe.get_all("Restaurant Menu Item", filters={"enabled": 1}, fields=["item", "menu_name", "description", "image", "category", "kitchen_station", "preparation_minutes", "vegetarian", "spicy", "popular"], order_by="category, modified desc", limit_page_length=1000)
	item_codes = [row.item for row in menu_rows]
	items = {row.name: row for row in frappe.get_all("Item", filters={"name": ["in", item_codes or [""]], "disabled": 0, "is_sales_item": 1}, fields=["name", "item_name", "stock_uom", "is_stock_item", "image"], limit_page_length=max(len(item_codes), 1))}
	stock, rates = _stock_and_prices(item_codes, context)
	allow_negative = cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock"))
	menu_result = []
	for row in menu_rows:
		item = items.get(row.item)
		if not item or row.item not in rates:
			continue
		available_qty = stock.get(row.item, 0)
		menu_result.append({"item_code": row.item, "item_name": row.menu_name or item.item_name, "description": row.description or "", "image": row.image or item.image or "", "category": row.category, "uom": item.stock_uom, "rate": rates[row.item], "currency": context.currency, "available_qty": available_qty, "available": bool(not item.is_stock_item or allow_negative or available_qty > 0), "preparation_minutes": row.preparation_minutes, "vegetarian": bool(row.vegetarian), "spicy": bool(row.spicy), "popular": bool(row.popular), "kitchen_station": row.kitchen_station, "modifier_groups": _modifier_configuration(row.item)})
	tables = []
	for row in table_rows:
		order = active.get(row.name)
		tables.append({**row, "status": _table_status(order), "guests": order.guest_count if order else 0, "waiter": order.waiter if order else None, "order": order.name if order else None, "amount": flt(order.grand_total, 2) if order else 0, "opened_at": order.opened_at if order else None})
	return {"user": user, "full_name": frappe.utils.get_fullname(user), "profile": context, "profiles": profiles, "branches": sorted({row.branch for row in profiles if row.branch}), "floors": floor_rows, "tables": tables, "categories": sorted({row.category for row in menu_rows}), "items": menu_result, "online": True, "server_time": now_datetime()}


@frappe.whitelist()
def get_table_order(table):
	_require_roles(_WAITER_ROLES)
	row = frappe.db.get_value("Restaurant Order", {"restaurant_table": table, "status": ["in", _ACTIVE_ORDER_STATUSES]}, "name")
	if not row:
		return {"order": None}
	return {"order": _order_response(_load_order(row))}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def open_order(branch, floor, table, guest_count, restaurant_profile=None):
	user, _roles = _require_roles(_WAITER_ROLES)
	context = _context(branch, restaurant_profile)
	if cint(guest_count) < 1:
		frappe.throw(_("Guest count must be greater than zero"))
	frappe.db.sql("select name from `tabRestaurant Table` where name=%s for update", table)
	table_doc = frappe.get_doc("Restaurant Table", table)
	if table_doc.disabled or table_doc.branch != context.branch or table_doc.floor != floor:
		frappe.throw(_("Restaurant Table is not available in this Floor"))
	existing = frappe.db.get_value("Restaurant Order", {"restaurant_table": table, "status": ["in", _ACTIVE_ORDER_STATUSES]}, "name")
	if existing:
		return {"order": _order_response(_load_order(existing)), "existing": True}
	doc = frappe.get_doc({"doctype":"Restaurant Order", "restaurant_profile":context.profile, "branch":context.branch, "company":context.company, "floor":floor, "restaurant_table":table, "waiter":user, "guest_count":cint(guest_count), "status":"Open", "opened_at":now_datetime(), "pos_profile":context.pos_profile, "customer":context.customer, "warehouse":context.warehouse, "price_list":context.price_list, "currency":context.currency})
	doc.insert(ignore_permissions=True)
	return {"order": _order_response(doc), "existing": False}


@frappe.whitelist()
@rate_limit(limit=120, seconds=60)
def save_order(order, items):
	doc = _lock_order(order)
	if doc.status in ("Closed", "Cancelled", "Bill Requested"):
		frappe.throw(_("This Restaurant Order cannot be edited"))
	context = _context(doc.branch, doc.restaurant_profile)
	rows = _parse_json_list(items, "items")
	if not rows:
		frappe.throw(_("Add at least one item"))
	menu_items = {row.item: row for row in frappe.get_all("Restaurant Menu Item", filters={"enabled": 1, "item": ["in", [x.get("item_code") for x in rows if isinstance(x, dict)] or [""]]}, fields=["item", "kitchen_station"], limit_page_length=len(rows))}
	stock, rates = _stock_and_prices(list(menu_items), context)
	for value in rows:
		if not isinstance(value, dict) or not value.get("item_code") or flt(value.get("qty")) <= 0:
			frappe.throw(_("Every Restaurant item needs an item and quantity"))
		item_code = value["item_code"]
		menu_item = menu_items.get(item_code)
		if not menu_item or item_code not in rates:
			frappe.throw(_("Item {0} is unavailable on this Restaurant menu").format(item_code))
		item = frappe.get_cached_doc("Item", item_code)
		if item.is_stock_item and not cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock")) and stock.get(item_code, 0) + 0.0001 < flt(value["qty"]):
			frappe.throw(_("Requested quantity for {0} is unavailable").format(item_code))
		modifiers, adjustment = _validate_modifiers(item_code, value.get("modifiers"))
		notes = strip_html_tags(str(value.get("notes") or "").strip())[:500]
		signature = json.dumps(modifiers, separators=(",", ":"), sort_keys=True)
		existing = next((row for row in doc.items if not row.sent_qty and row.item == item_code and (row.modifiers_json or "[]") == signature and (row.notes or "") == notes), None)
		if existing:
			existing.qty = flt(existing.qty) + flt(value["qty"])
		else:
			doc.append("items", {"item":item_code, "item_name":item.item_name, "uom":value.get("uom") or item.stock_uom, "qty":flt(value["qty"]), "sent_qty":0, "rate":flt(rates[item_code] + adjustment, 2), "notes":notes, "modifiers_json":signature, "kitchen_station":menu_item.kitchen_station, "kitchen_status":"Not Sent", "added_at":now_datetime()})
	doc.save(ignore_permissions=True)
	return {"order": _order_response(doc)}


@frappe.whitelist()
def update_unsent_item(order, row_id, quantity, notes=None):
	doc = _lock_order(order)
	row = next((row for row in doc.items if row.name == row_id), None)
	if not row:
		frappe.throw(_("Restaurant Order item was not found"), frappe.DoesNotExistError)
	if flt(row.sent_qty):
		frappe.throw(_("Sent Restaurant items cannot be edited directly"))
	if flt(quantity) <= 0:
		doc.remove(row)
	else:
		row.qty = flt(quantity)
		if notes is not None:
			row.notes = strip_html_tags(str(notes).strip())[:500]
	doc.save(ignore_permissions=True)
	return {"order": _order_response(doc)}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def send_to_kitchen(order, request_id):
	if not request_id or len(request_id) > 140:
		frappe.throw(_("A valid kitchen request ID is required"))
	existing = frappe.db.get_value("Restaurant Kitchen Ticket", {"request_id": request_id}, ["name", "restaurant_order"], as_dict=True)
	if existing:
		if existing.restaurant_order != order:
			frappe.throw(_("This kitchen request ID belongs to another order"), frappe.PermissionError)
		_load_order(order)
		return {"ticket": existing.name, "duplicate": True}
	doc = _lock_order(order)
	# Recheck while the order row is locked. The unique request_id protects the
	# database too, while this check produces a deterministic idempotent result
	# for two near-simultaneous mobile retries.
	existing = frappe.db.get_value("Restaurant Kitchen Ticket", {"request_id": request_id}, ["name", "restaurant_order"], as_dict=True)
	if existing:
		if existing.restaurant_order != order:
			frappe.throw(_("This kitchen request ID belongs to another order"), frappe.PermissionError)
		return {"ticket": existing.name, "duplicate": True, "order": _order_response(doc)}
	if doc.status in ("Closed", "Cancelled", "Bill Requested"):
		frappe.throw(_("This order cannot be sent to the kitchen"))
	pending = [row for row in doc.items if flt(row.qty) > flt(row.sent_qty)]
	if not pending:
		frappe.throw(_("There are no new quantities to send"))
	ticket = frappe.get_doc({"doctype":"Restaurant Kitchen Ticket", "request_id":request_id, "restaurant_order":doc.name, "status":"Queued", "branch":doc.branch, "floor":doc.floor, "restaurant_table":doc.restaurant_table, "waiter":doc.waiter, "sent_at":now_datetime()})
	for row in pending:
		qty = flt(row.qty) - flt(row.sent_qty)
		ticket.append("items", {"order_item_row":row.name, "item":row.item, "item_name":row.item_name, "qty":qty, "notes":row.notes, "modifiers_json":row.modifiers_json or "[]", "kitchen_station":row.kitchen_station})
		row.sent_qty = row.qty
		row.kitchen_status = "Queued"
	doc.status = "Sent to Kitchen"
	doc.save(ignore_permissions=True)
	ticket.insert(ignore_permissions=True)
	return {"ticket": ticket.name, "status": ticket.status, "order": _order_response(doc), "duplicate": False}


@frappe.whitelist()
def update_kitchen_status(ticket, status):
	_require_roles(_KITCHEN_ROLES)
	if status not in _KITCHEN_FLOW and status != "Cancelled":
		frappe.throw(_("Invalid kitchen status"))
	doc = frappe.get_doc("Restaurant Kitchen Ticket", ticket)
	profiles = {row.name for row in _permitted_profiles(doc.branch)}
	order = frappe.get_doc("Restaurant Order", doc.restaurant_order)
	if order.restaurant_profile not in profiles:
		frappe.throw(_("You are not permitted to update this Kitchen Ticket"), frappe.PermissionError)
	doc.status = status
	doc.save(ignore_permissions=True)
	for ticket_row in doc.items:
		order_row = next((row for row in order.items if row.name == ticket_row.order_item_row), None)
		if order_row:
			order_row.kitchen_status = status
	order.save(ignore_permissions=True)
	return {"ticket": doc.name, "status": doc.status, "order": _order_response(order)}


@frappe.whitelist()
def request_bill(order):
	doc = _lock_order(order)
	if not doc.items:
		frappe.throw(_("Cannot request a bill for an empty order"))
	if any(flt(row.qty) > flt(row.sent_qty) for row in doc.items):
		frappe.throw(_("Send all new items to the kitchen before requesting the bill"))
	if doc.status in ("Closed", "Cancelled"):
		frappe.throw(_("This order cannot request a bill"))
	doc.status = "Bill Requested"
	doc.bill_requested_at = now_datetime()
	doc.save(ignore_permissions=True)
	return {"order": _order_response(doc)}


@frappe.whitelist()
def close_table(order, pos_invoice):
	doc = _lock_order(order)
	if doc.status != "Bill Requested":
		frappe.throw(_("Request the bill before closing the table"))
	invoice = frappe.get_doc("POS Invoice", pos_invoice)
	if invoice.docstatus != 1 or invoice.company != doc.company or invoice.pos_profile != doc.pos_profile:
		frappe.throw(_("POS Invoice does not match this Restaurant Order"))
	if doc.customer and invoice.customer != doc.customer:
		frappe.throw(_("POS Invoice Customer does not match this Restaurant Order"))
	if doc.branch and invoice.get("branch") and invoice.branch != doc.branch:
		frappe.throw(_("POS Invoice Branch does not match this Restaurant Order"))
	if frappe.db.exists("Restaurant Order", {"name":["!=", doc.name], "pos_invoice":invoice.name}):
		frappe.throw(_("This POS Invoice already closed another Restaurant Order"))
	ordered = defaultdict(float)
	invoiced = defaultdict(float)
	for row in doc.items:
		ordered[row.item] += flt(row.qty)
	for row in invoice.items:
		invoiced[row.item_code] += flt(row.qty)
	if any(invoiced[item] + 0.0001 < qty for item, qty in ordered.items()):
		frappe.throw(_("POS Invoice does not contain all Restaurant Order quantities"))
	doc.status = "Closed"
	doc.pos_invoice = invoice.name
	doc.closed_at = now_datetime()
	doc.save(ignore_permissions=True)
	return {"order": _order_response(doc)}


@frappe.whitelist()
def get_orders(branch=None, restaurant_profile=None, status=None, offset=0, limit=50):
	profiles = _permitted_profiles(branch)
	profile_names = [row.name for row in profiles]
	if restaurant_profile:
		profile_names = [name for name in profile_names if name == restaurant_profile]
	filters = {"restaurant_profile": ["in", profile_names or [""]]}
	if status:
		filters["status"] = status
	rows = frappe.get_all("Restaurant Order", filters=filters, pluck="name", order_by="modified desc", limit_start=cint(offset), limit_page_length=min(max(cint(limit) or 50, 1), 100))
	return {"orders": [_order_response(frappe.get_doc("Restaurant Order", name)) for name in rows]}


@frappe.whitelist()
def get_activity(branch=None, restaurant_profile=None, limit=50):
	profiles = _permitted_profiles(branch)
	profile_names = [row.name for row in profiles]
	if restaurant_profile:
		profile_names = [name for name in profile_names if name == restaurant_profile]
	orders = frappe.get_all("Restaurant Order", filters={"restaurant_profile":["in", profile_names or [""]]}, fields=["name", "restaurant_table", "waiter", "status", "modified"], order_by="modified desc", limit_page_length=min(max(cint(limit) or 50, 1), 100))
	return {"activity": [{"type":"Order", "order":row.name, "table":row.restaurant_table, "waiter":row.waiter, "status":row.status, "at":row.modified} for row in orders]}
