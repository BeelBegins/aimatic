import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

# A larger-UOM price (Box) may be cheaper per piece than the stock-UOM price
# (Pcs), but never by half or more. Below this ratio the Box price is almost
# certainly a Pcs price keyed against the Box UOM.
MIN_UOM_PRICE_RATIO = 0.5


def get_stock_uom_price(item_code, stock_uom, price_list, as_of):
	"""Latest valid selling price of the item in its stock UOM, or 0."""
	rows = frappe.db.sql(
		"""
		select price_list_rate from `tabItem Price`
		where item_code = %(item)s and price_list = %(pl)s and selling = 1
			and uom = %(uom)s
			and (valid_from is null or valid_from <= %(d)s)
			and (valid_upto is null or valid_upto >= %(d)s)
		order by valid_from desc, modified desc limit 1
		""",
		{"item": item_code, "pl": price_list, "uom": stock_uom, "d": as_of},
	)
	return flt(rows[0][0]) if rows else 0


def _fmt(value):
	return f"{flt(value):,.2f}"


def _is_below_floor(price_per_uom, stock_uom_price, conversion_factor):
	expected = stock_uom_price * conversion_factor
	return expected > 0 and price_per_uom < expected * MIN_UOM_PRICE_RATIO


def validate_pos_uom_pricing(doc, method=None):
	"""Block a sale row whose larger-UOM price is far below stock-UOM price x conversion.

	Stops "28 Box at Rs 99" (a Pcs price on a 28-piece Box) from being sold: it
	over-issues stock and, on third-schedule items, breaks the FBR tax split.
	Returns are exempt so an existing sale can always be reversed.
	"""
	if cint(getattr(doc, "is_return", 0)) or not getattr(doc, "selling_price_list", None):
		return

	as_of = getdate(getattr(doc, "posting_date", None))

	for row in doc.get("items") or []:
		conversion_factor = abs(flt(row.get("conversion_factor")) or 1)
		stock_uom = row.get("stock_uom")
		if not row.get("item_code") or conversion_factor <= 1 or not stock_uom or row.get("uom") == stock_uom:
			continue

		stock_price = get_stock_uom_price(row.item_code, stock_uom, doc.selling_price_list, as_of)
		if _is_below_floor(flt(row.get("rate")), stock_price, conversion_factor):
			frappe.throw(
				_(
					"Row {0}: {1} is sold per {2} (1 {2} = {3} {4}) at {5}, but one {4} sells for {6}. "
					"That is below half of the expected {2} price. Check the UOM or fix the {2} price in "
					"{7}."
				).format(
					row.idx,
					frappe.bold(row.item_code),
					row.uom,
					conversion_factor,
					stock_uom,
					_fmt(flt(row.rate)),
					_fmt(stock_price),
					frappe.bold(doc.selling_price_list),
				),
				title=_("UOM price mismatch"),
			)


def validate_item_price_uom(doc, method=None):
	"""Block saving a larger-UOM selling price far below stock-UOM price x conversion."""
	if not cint(doc.selling) or not doc.item_code or not doc.uom or not doc.price_list:
		return

	stock_uom = frappe.db.get_value("Item", doc.item_code, "stock_uom")
	if not stock_uom or doc.uom == stock_uom:
		return

	conversion_factor = flt(
		frappe.db.get_value(
			"UOM Conversion Detail", {"parent": doc.item_code, "uom": doc.uom}, "conversion_factor"
		)
	)
	if conversion_factor <= 1:
		return

	as_of = getdate(doc.valid_from) if doc.valid_from else getdate()
	stock_price = get_stock_uom_price(doc.item_code, stock_uom, doc.price_list, as_of)
	if _is_below_floor(flt(doc.price_list_rate), stock_price, conversion_factor):
		frappe.throw(
			_(
				"{0} price {1} for {2} is below half of {3} x {4} = {5} ({6} price {3} x conversion factor). "
				"It looks like a {6} price entered against {0}."
			).format(
				doc.uom,
				_fmt(flt(doc.price_list_rate)),
				frappe.bold(doc.item_code),
				_fmt(stock_price),
				conversion_factor,
				_fmt(stock_price * conversion_factor),
				stock_uom,
			),
			title=_("UOM price mismatch"),
		)
