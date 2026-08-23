"""Idempotent, explicitly prefixed data for exercising Ai Matic Sales end to end.

This module is never run during install or migrate. Invoke ``execute`` deliberately on a
sandbox/pilot site; it creates isolated demo masters, stock, orders, approval, and visits.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, now_datetime, nowdate

DEMO_PREFIX = "AIMATIC DEMO"


def _company(value=None):
	company = value or frappe.defaults.get_global_default("default_company")
	if company:
		return company
	companies = frappe.get_all("Company", pluck="name", limit_page_length=2)
	if len(companies) != 1:
		frappe.throw(_("Pass company when the site has more than one Company"))
	return companies[0]


def _leaf_value(doctype, filters):
	return frappe.db.get_value(doctype, filters, "name")


def _ensure_warehouse(company):
	existing = _leaf_value("Warehouse", {"warehouse_name": "Mobile Sales Demo", "company": company})
	if existing:
		return existing
	parent = _leaf_value(
		"Warehouse", {"company": company, "is_group": 1, "parent_warehouse": ["is", "not set"]}
	)
	if not parent:
		frappe.throw(_("No root Warehouse exists for {0}").format(company))
	return (
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": "Mobile Sales Demo",
				"company": company,
				"parent_warehouse": parent,
				"is_group": 0,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_brand(name):
	if not frappe.db.exists("Brand", name):
		frappe.get_doc({"doctype": "Brand", "brand": name}).insert(ignore_permissions=True)
	return name


def _ensure_item_group():
	name = "Mobile Sales Demo"
	if not frappe.db.exists("Item Group", name):
		frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": name,
				"parent_item_group": "All Item Groups",
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
	return name


def _ensure_item(spec, company, warehouse, price_list, item_group):
	code = spec["item_code"]
	if frappe.db.exists("Item", code):
		doc = frappe.get_doc("Item", code)
		doc.item_name = spec["item_name"]
		doc.item_group = item_group
		doc.brand = spec["brand"]
		doc.disabled = 0
		doc.is_sales_item = 1
		if not any(row.uom == "Box" for row in doc.uoms):
			doc.append("uoms", {"uom": "Box", "conversion_factor": spec["box_factor"]})
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": spec["item_name"],
				"description": "Clearly marked Ai Matic Sales demo item. Not real merchandise.",
				"item_group": item_group,
				"brand": spec["brand"],
				"stock_uom": "Pcs",
				"sales_uom": "Box",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"is_purchase_item": 0,
				"standard_rate": spec["piece_rate"],
				"uoms": [{"uom": "Box", "conversion_factor": spec["box_factor"]}],
				"item_defaults": [
					{
						"company": company,
						"default_warehouse": warehouse,
						"default_price_list": price_list,
					}
				],
			}
		).insert(ignore_permissions=True)
	for uom, rate in (("Pcs", spec["piece_rate"]), ("Box", spec["piece_rate"] * spec["box_factor"] * 0.95)):
		filters = {"item_code": code, "price_list": price_list, "uom": uom, "selling": 1}
		price = frappe.db.get_value("Item Price", filters, "name")
		if price:
			frappe.db.set_value(
				"Item Price", price, {"price_list_rate": rate, "valid_from": add_days(nowdate(), -90)}
			)
		else:
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"item_code": code,
					"price_list": price_list,
					"selling": 1,
					"uom": uom,
					"price_list_rate": rate,
					"valid_from": add_days(nowdate(), -90),
				}
			).insert(ignore_permissions=True)
	return doc.name


def _ensure_stock(item_code, warehouse, company, branch, target_qty, valuation_rate):
	actual = flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
	if actual >= target_qty:
		return None
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	entry = make_stock_entry(
		item_code=item_code,
		to_warehouse=warehouse,
		qty=target_qty - actual,
		rate=valuation_rate,
		company=company,
		do_not_save=True,
	)
	if branch and entry.meta.has_field("branch"):
		entry.branch = branch
	entry.insert(ignore_permissions=True)
	entry.submit()
	entry.add_comment("Comment", _("Ai Matic Sales demo opening stock"))
	return entry.name


def _ensure_customer(spec, price_list, company):
	name = frappe.db.get_value("Customer", {"customer_name": spec["customer_name"]}, "name")
	if name:
		doc = frappe.get_doc("Customer", name)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": spec["customer_name"],
				"customer_type": "Company",
				"customer_group": "Commercial",
				"territory": "Pakistan",
				"default_price_list": price_list,
			}
		).insert(ignore_permissions=True)
	if not any(row.company == company for row in doc.credit_limits):
		doc.append(
			"credit_limits",
			{
				"company": company,
				"credit_limit": spec["credit_limit"],
				"bypass_credit_limit_check": 0,
			},
		)
	else:
		for row in doc.credit_limits:
			if row.company == company:
				row.credit_limit = spec["credit_limit"]
	doc.save(ignore_permissions=True)
	return doc.name


def _ensure_address(customer, index):
	title = f"{customer} Demo Delivery"
	name = frappe.db.get_value("Address", {"address_title": title}, "name")
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": title,
				"address_type": "Shipping",
				"address_line1": f"Demo Shop {index}, Main Market",
				"city": "Islamabad",
				"country": "Pakistan",
				"links": [{"link_doctype": "Customer", "link_name": customer}],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_delivery_location(customer, address, index):
	name = frappe.db.get_value(
		"Mobile Sales Delivery Location", {"customer": customer, "address": address}, "name"
	)
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Mobile Sales Delivery Location",
				"customer": customer,
				"location_name": f"Demo Outlet {index}",
				"address": address,
				"enabled": 1,
				"is_default": 1,
				"monday": 1,
				"wednesday": 1,
				"friday": 1,
				"minimum_order_value": 5000 if index == 1 else 1000,
				"instructions": "Demo only: deliver to the marked receiving counter.",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_assortment(customer, item=None, item_group=None):
	filters = {
		"customer": customer,
		"item": item or ["is", "not set"],
		"item_group": item_group or ["is", "not set"],
	}
	name = frappe.db.get_value("Mobile Sales Assortment", filters, "name")
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Mobile Sales Assortment",
				"customer": customer,
				"enabled": 1,
				"item": item,
				"item_group": item_group,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_discount_authority(user):
	name = frappe.db.get_value("Mobile Sales Discount Authority", {"user": user}, "name")
	if name:
		frappe.db.set_value(
			"Mobile Sales Discount Authority", name, {"enabled": 1, "maximum_discount_percent": 5}
		)
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Mobile Sales Discount Authority",
				"user": user,
				"enabled": 1,
				"maximum_discount_percent": 5,
				"notes": "Ai Matic Sales demo authority. Orders above 5% require approval.",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_promotion(company, currency, warehouse, brand):
	title = "AIMATIC DEMO Brand Offer"
	name = frappe.db.get_value("Pricing Rule", {"title": title}, "name")
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": title,
				"apply_on": "Brand",
				"price_or_product_discount": "Price",
				"rate_or_discount": "Discount Percentage",
				"discount_percentage": 7.5,
				"selling": 1,
				"company": company,
				"currency": currency,
				"warehouse": warehouse,
				"valid_from": nowdate(),
				"valid_upto": add_days(nowdate(), 90),
				"brands": [{"brand": brand}],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_order(
	reference,
	company,
	branch,
	warehouse,
	price_list,
	customer,
	items,
	transaction_date,
	discount=0,
	submit=False,
):
	name = frappe.db.get_value("Sales Order", {"customer": customer, "po_no": reference}, "name")
	if name:
		doc = frappe.get_doc("Sales Order", name)
		if doc.docstatus == 0 and doc.meta.has_field("custom_mobile_sales_notes"):
			doc.custom_mobile_sales_notes = "Ai Matic Sales demo order for action testing."
			doc.save(ignore_permissions=True)
		return doc
	doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": company,
			"customer": customer,
			"transaction_date": transaction_date,
			"delivery_date": add_days(transaction_date, 7),
			"set_warehouse": warehouse,
			"selling_price_list": price_list,
			"po_no": reference,
			"custom_mobile_sales_notes": "Ai Matic Sales demo order for action testing.",
			"apply_discount_on": "Grand Total",
			"additional_discount_percentage": discount,
			"items": [
				{**row, "warehouse": warehouse, "delivery_date": add_days(transaction_date, 7)}
				for row in items
			],
		}
	)
	if branch and doc.meta.has_field("branch"):
		doc.branch = branch
	doc.insert(ignore_permissions=True)
	if submit:
		doc.submit()
	return doc


def _ensure_pending_approval(order, requested_by):
	name = frappe.db.get_value("Mobile Sales Discount Approval", {"sales_order": order.name}, "name")
	if name:
		frappe.db.set_value(
			"Mobile Sales Discount Approval", name, {"status": "Pending", "requested_by": requested_by}
		)
		return name
	original = flt(order.grand_total + order.discount_amount, 2)
	return (
		frappe.get_doc(
			{
				"doctype": "Mobile Sales Discount Approval",
				"sales_order": order.name,
				"status": "Pending",
				"requested_by": requested_by,
				"requested_percent": flt(order.additional_discount_percentage, 3),
				"authority_percent": 5,
				"reason": "Demo approval: customer requested launch support.",
				"requested_at": now_datetime(),
				"original_grand_total": original,
				"requested_grand_total": flt(order.grand_total, 2),
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_visit(company, warehouse, customer, address, assigned_to, route_order):
	filters = {
		"customer": customer,
		"assigned_to": assigned_to,
		"scheduled_date": nowdate(),
		"instructions": ["like", f"{DEMO_PREFIX}%"],
	}
	name = frappe.db.get_value("Mobile Sales Visit", filters, "name")
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Mobile Sales Visit",
				"company": company,
				"warehouse": warehouse,
				"customer": customer,
				"assigned_to": assigned_to,
				"status": "Planned",
				"scheduled_date": nowdate(),
				"scheduled_time": f"{9 + route_order}:00:00",
				"route_order": route_order,
				"customer_address": address,
				"planned_latitude": 33.6844 + route_order * 0.004,
				"planned_longitude": 73.0479 + route_order * 0.004,
				"instructions": f"{DEMO_PREFIX}: test GPS check-in, notes, photo, and check-out.",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def execute(company=None, assigned_to=None):
	"""Create or refresh a complete, isolated Mobile Sales demo scenario."""
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("System Manager is required to create Mobile Sales demo data"), frappe.PermissionError)
	company = _company(company)
	assigned_to = assigned_to or frappe.session.user
	user = frappe.db.get_value("User", assigned_to, ["enabled", "user_type"], as_dict=True)
	if not user or not user.enabled or user.user_type != "System User":
		frappe.throw(_("assigned_to must be an enabled System User"))
	if not {"Sales User", "Sales Manager", "System Manager"}.intersection(frappe.get_roles(assigned_to)):
		frappe.throw(_("assigned_to must have a Sales role"))
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	currency = frappe.db.get_value("Price List", price_list, "currency") or frappe.db.get_value(
		"Company", company, "default_currency"
	)
	branch = frappe.db.get_value("Branch", {"company": company}, "name")
	warehouse = _ensure_warehouse(company)
	item_group = _ensure_item_group()
	brands = [_ensure_brand("AIMATIC Demo Tools"), _ensure_brand("AIMATIC Demo Essentials")]
	item_specs = [
		{
			"item_code": "AIM-DEMO-DRILL",
			"item_name": "AIMATIC Demo Cordless Drill",
			"brand": brands[0],
			"box_factor": 6,
			"piece_rate": 1800,
			"stock": 120,
			"valuation": 1100,
		},
		{
			"item_code": "AIM-DEMO-GLOVES",
			"item_name": "AIMATIC Demo Safety Gloves",
			"brand": brands[1],
			"box_factor": 12,
			"piece_rate": 250,
			"stock": 240,
			"valuation": 125,
		},
		{
			"item_code": "AIM-DEMO-TAPE",
			"item_name": "AIMATIC Demo Measuring Tape",
			"brand": brands[0],
			"box_factor": 10,
			"piece_rate": 600,
			"stock": 80,
			"valuation": 350,
		},
		{
			"item_code": "AIM-DEMO-OOS",
			"item_name": "AIMATIC Demo Out of Stock Item",
			"brand": brands[1],
			"box_factor": 4,
			"piece_rate": 900,
			"stock": 0,
			"valuation": 500,
		},
	]
	items = []
	stock_entries = []
	for spec in item_specs:
		items.append(_ensure_item(spec, company, warehouse, price_list, item_group))
		if spec["stock"]:
			entry = _ensure_stock(
				spec["item_code"], warehouse, company, branch, spec["stock"], spec["valuation"]
			)
			if entry:
				stock_entries.append(entry)
	customer_specs = [
		{"customer_name": "AIMATIC Demo Retail Outlet", "credit_limit": 150000},
		{"customer_name": "AIMATIC Demo Wholesale", "credit_limit": 100000},
		{"customer_name": "AIMATIC Demo Credit Warning", "credit_limit": 1000},
		{"customer_name": "AIMATIC Demo New Customer", "credit_limit": 0},
	]
	customers = [_ensure_customer(spec, price_list, company) for spec in customer_specs]
	addresses = [_ensure_address(customer, index + 1) for index, customer in enumerate(customers)]
	delivery_locations = [
		_ensure_delivery_location(customer, addresses[index], index + 1)
		for index, customer in enumerate(customers[:2])
	]
	assortments = [
		_ensure_assortment(customers[0], item_group=item_group),
		_ensure_assortment(customers[1], item=items[0]),
		_ensure_assortment(customers[1], item=items[1]),
	]
	authority = _ensure_discount_authority(assigned_to)
	promotion = _ensure_promotion(company, currency, warehouse, brands[0])
	order_items = [
		{"item_code": items[0], "qty": 1, "uom": "Box"},
		{"item_code": items[1], "qty": 2, "uom": "Box"},
	]
	editable = _ensure_order(
		"AIMATIC-DEMO-EDIT",
		company,
		branch,
		warehouse,
		price_list,
		customers[0],
		order_items,
		nowdate(),
		discount=4,
	)
	pending = _ensure_order(
		"AIMATIC-DEMO-APPROVAL",
		company,
		branch,
		warehouse,
		price_list,
		customers[1],
		order_items,
		nowdate(),
		discount=12,
	)
	approval = _ensure_pending_approval(pending, assigned_to)
	history_one = _ensure_order(
		"AIMATIC-DEMO-HISTORY-1",
		company,
		branch,
		warehouse,
		price_list,
		customers[0],
		[{"item_code": items[0], "qty": 1, "uom": "Box"}],
		add_days(nowdate(), -14),
		submit=True,
	)
	history_two = _ensure_order(
		"AIMATIC-DEMO-HISTORY-2",
		company,
		branch,
		warehouse,
		price_list,
		customers[0],
		[{"item_code": items[0], "qty": 2, "uom": "Box"}],
		add_days(nowdate(), -7),
		submit=True,
	)
	history_three = _ensure_order(
		"AIMATIC-DEMO-HISTORY-3",
		company,
		branch,
		warehouse,
		price_list,
		customers[0],
		[{"item_code": items[0], "qty": 1, "uom": "Box"}],
		nowdate(),
		submit=True,
	)
	history_four = _ensure_order(
		"AIMATIC-DEMO-HISTORY-4",
		company,
		branch,
		warehouse,
		price_list,
		customers[0],
		[{"item_code": items[0], "qty": 3, "uom": "Box"}],
		nowdate(),
		submit=True,
	)
	cancel_candidate = _ensure_order(
		"AIMATIC-DEMO-CANCEL",
		company,
		branch,
		warehouse,
		price_list,
		customers[3],
		[{"item_code": items[2], "qty": 2, "uom": "Pcs"}],
		nowdate(),
		submit=True,
	)
	cancelled_sample = _ensure_order(
		"AIMATIC-DEMO-CANCELLED",
		company,
		branch,
		warehouse,
		price_list,
		customers[3],
		[{"item_code": items[2], "qty": 1, "uom": "Pcs"}],
		nowdate(),
		submit=True,
	)
	if cancelled_sample.docstatus == 1:
		from aimatic.mobile_sales.api import cancel_order

		cancel_order(cancelled_sample.name)
		cancelled_sample.reload()
	visits = [
		_ensure_visit(company, warehouse, customer, addresses[index], assigned_to, index + 1)
		for index, customer in enumerate(customers[:2])
	]
	frappe.db.commit()
	return {
		"company": company,
		"warehouse": warehouse,
		"assigned_to": assigned_to,
		"customers": customers,
		"items": items,
		"brands": brands,
		"stock_entries_created": stock_entries,
		"delivery_locations": delivery_locations,
		"assortments": assortments,
		"discount_authority": authority,
		"pending_approval": approval,
		"promotion": promotion,
		"orders": [
			editable.name,
			pending.name,
			history_one.name,
			history_two.name,
			history_three.name,
			history_four.name,
			cancel_candidate.name,
			cancelled_sample.name,
		],
		"cancel_candidate": cancel_candidate.name,
		"visits": visits,
	}
