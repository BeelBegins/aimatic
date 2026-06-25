import frappe
from frappe import _
from frappe.utils import cint, flt


MONEY_PRECISION = 2
QTY_PRECISION = 3


def money(value):
    return flt(value, MONEY_PRECISION)


def quantity(value):
    return flt(value, QTY_PRECISION)


def get_item_fbr_configuration(item_code):
    item = frappe.db.get_value(
        "Item",
        item_code,
        [
            "item_name",
            "custom_mrp",
            "custom_is_3rd_schedule",
            "custom_fbr_tax_category",
        ],
        as_dict=True,
    )

    if not item:
        frappe.throw(
            _("Item {0} was not found").format(
                frappe.bold(item_code)
            )
        )

    if not item.custom_fbr_tax_category:
        frappe.throw(
            _("FBR Tax Category is missing for item {0}").format(
                frappe.bold(item_code)
            )
        )

    category = frappe.db.get_value(
        "FBR Tax Category",
        item.custom_fbr_tax_category,
        [
            "enabled",
            "tax_rate",
            "fbr_sale_type",
            "is_third_schedule",
            "is_exempt",
            "is_zero_rated",
        ],
        as_dict=True,
    )

    if not category:
        frappe.throw(
            _("FBR Tax Category {0} was not found").format(
                frappe.bold(item.custom_fbr_tax_category)
            )
        )

    if not cint(category.enabled):
        frappe.throw(
            _("FBR Tax Category {0} is disabled").format(
                frappe.bold(item.custom_fbr_tax_category)
            )
        )

    tax_rate = flt(category.tax_rate)
    mrp = flt(item.custom_mrp)

    # Item field is updated from Purchase Invoice.
    # Category should also match it, but Item field is the operational flag.
    is_third_schedule = cint(item.custom_is_3rd_schedule)

    if is_third_schedule and not cint(category.is_third_schedule):
        frappe.throw(
            _(
                "Item {0} is marked as Third Schedule, "
                "but selected FBR Tax Category is not Third Schedule"
            ).format(frappe.bold(item_code))
        )

    if is_third_schedule and mrp <= 0:
        frappe.throw(
            _("MRP is required for Third Schedule item {0}").format(
                frappe.bold(item_code)
            )
        )

    return {
        "item_code": item_code,
        "item_name": item.item_name,
        "tax_category": item.custom_fbr_tax_category,
        "tax_rate": tax_rate,
        "fbr_sale_type": category.fbr_sale_type or "",
        "is_third_schedule": is_third_schedule,
        "is_exempt": cint(category.is_exempt),
        "is_zero_rated": cint(category.is_zero_rated),
        "mrp": mrp,
    }


def get_row_inclusive_amount(row):
    """
    Use customer selling amount, not ERPNext net_amount.

    row.net_amount changes after ERPNext inclusive tax calculation.
    If we use net_amount, FBR values keep recalculating lower and lower.
    row.amount remains the customer item selling price.
    """
    return abs(money(row.amount or (row.rate * row.qty)))


def calculate_fbr_item(row, config):
    qty = abs(quantity(row.qty))
    inclusive_value = get_row_inclusive_amount(row)

    tax_rate = flt(config["tax_rate"])
    is_exempt = cint(config["is_exempt"])
    is_zero_rated = cint(config["is_zero_rated"])
    is_third_schedule = cint(config["is_third_schedule"])
    mrp = flt(config["mrp"])

    if qty <= 0:
        frappe.throw(
            _("Quantity must be greater than zero for item {0}").format(
                frappe.bold(row.item_code)
            )
        )

    if is_exempt or is_zero_rated or tax_rate <= 0:
        return {
            "quantity": qty,
            "inclusive_value": inclusive_value,
            "value_excluding_tax": inclusive_value,
            "sales_tax": 0,
            "retail_price": 0,
        }
    if is_third_schedule:
        # custom_mrp is maintained per stock UOM.  A sales row can use a
        # larger UOM (for example, a 12-piece box), so its MRP tax base must
        # include the sales-UOM conversion factor.
        conversion_factor = abs(flt(getattr(row, "conversion_factor", 1)) or 1)
        retail_price = money(mrp * qty * conversion_factor)

        sales_tax = money(
            retail_price * tax_rate / (100 + tax_rate)
        )

        value_excluding_tax = money(
            inclusive_value - sales_tax
        )

        return {
            "quantity": qty,
            "inclusive_value": inclusive_value,
            "value_excluding_tax": value_excluding_tax,
            "sales_tax": sales_tax,
            "retail_price": retail_price,
        }

    # Regular (non-third-schedule) items: tax is calculated from inclusive value
    sales_tax = money(
        inclusive_value * tax_rate / (100 + tax_rate)
    )

    return {
        "quantity": qty,
        "inclusive_value": inclusive_value,
        "value_excluding_tax": money(inclusive_value - sales_tax),
        "sales_tax": sales_tax,
        "retail_price": 0,
    }
