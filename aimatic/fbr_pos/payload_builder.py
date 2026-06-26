import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from aimatic.fbr_pos.settings import get_fbr_settings
from aimatic.fbr_pos.tax_calculator import (
    calculate_fbr_item,
    get_item_fbr_configuration,
    money,
)


DEFAULT_HS_CODE = "01011000"


def get_invoice_branch(doc):
    """
    POS Invoice branch can be empty.
    If empty, use POS Profile branch.
    """

    if getattr(doc, "branch", None):
        return doc.branch

    if getattr(doc, "pos_profile", None):
        branch = frappe.db.get_value(
            "POS Profile",
            doc.pos_profile,
            "branch",
        )

        if branch:
            return branch

    frappe.throw(
        _("Branch is missing on POS Invoice and POS Profile")
    )


def get_settings_for_invoice(doc):
    branch = get_invoice_branch(doc)

    return get_fbr_settings(
        doc.company,
        branch,
    )


def get_customer_snapshot(doc):
    """
    Prefer POS Invoice customer tax snapshot.
    Fall back to Customer master.
    """

    customer_data = {}

    if getattr(doc, "customer", None):
        customer_data = frappe.db.get_value(
            "Customer",
            doc.customer,
            [
                "customer_name",
                "tax_strn",
                "tax_ntn",
                "tax_nic",
                "mobile_no",
            ],
            as_dict=True,
        ) or {}

    return {
        "name": (
            getattr(doc, "customer_name", None)
            or customer_data.get("customer_name")
            or getattr(doc, "customer", None)
            or ""
        ),
        "strn": (
            getattr(doc, "tax_strn", None)
            or customer_data.get("tax_strn")
            or ""
        ),
        "ntn": (
            getattr(doc, "tax_ntn", None)
            or customer_data.get("tax_ntn")
            or ""
        ),
        "cnic": (
            getattr(doc, "tax_nic", None)
            or customer_data.get("tax_nic")
            or ""
        ),
        "phone": (
            getattr(doc, "contact_mobile", None)
            or customer_data.get("mobile_no")
            or ""
        ),
    }


def get_payment_mode(doc, settings):
    """
    FBR POS payment mode.

    Initial logic:
    Cash = 1
    Card = 2
    Otherwise use default_payment_mode from FBR settings.
    """

    payments = doc.get("payments") or []

    if not payments:
        return cint(settings.get("default_payment_mode") or 1)

    primary_payment = max(
        payments,
        key=lambda row: flt(row.amount),
    )

    mode = (
        primary_payment.mode_of_payment or ""
    ).strip().lower()

    if "cash" in mode:
        return 1

    if "card" in mode:
        return 2

    return cint(settings.get("default_payment_mode") or 1)


def clean_hs_code(value):
    """
    FBR POS payload field is PCTCode.
    User-facing field is HS Code.

    Old working SAP integration required 8 digits and fell back
    to default when invalid.
    """

    code = str(value or "").strip()

    if len(code) == 8 and code.isdigit():
        return code

    return ""


def get_item_hs_code(row, settings):
    item_hs_code = ""

    if row.item_code:
        item_hs_code = frappe.db.get_value(
            "Item",
            row.item_code,
            "custom_fbr_hs_code",
        ) or ""

    hs_code = (
        clean_hs_code(item_hs_code)
        or clean_hs_code(settings.get("default_hs_code"))
        or DEFAULT_HS_CODE
    )

    return hs_code


def get_discount_amount(row):
    return abs(
        money(
            getattr(row, "discount_amount", 0)
        )
    )


def update_row_fbr_snapshot(row, config, calculated, hs_code):
    """
    Store exact values used in FBR payload on POS Invoice Item.
    """

    if row.meta.has_field("custom_fbr_tax_category"):
        row.custom_fbr_tax_category = config["tax_category"]

    if row.meta.has_field("custom_fbr_sale_type"):
        row.custom_fbr_sale_type = config["fbr_sale_type"]

    if row.meta.has_field("custom_fbr_tax_rate"):
        row.custom_fbr_tax_rate = config["tax_rate"]

    if row.meta.has_field("custom_fbr_is_third_schedule"):
        row.custom_fbr_is_third_schedule = config["is_third_schedule"]

    if row.meta.has_field("custom_fbr_mrp"):
        row.custom_fbr_mrp = config["mrp"]

    if row.meta.has_field("custom_fbr_hs_code"):
        row.custom_fbr_hs_code = hs_code

    if row.meta.has_field("custom_fbr_value_excluding_tax"):
        row.custom_fbr_value_excluding_tax = calculated[
            "value_excluding_tax"
        ]

    if row.meta.has_field("custom_fbr_sales_tax"):
        row.custom_fbr_sales_tax = calculated[
            "sales_tax"
        ]

    if row.meta.has_field("custom_fbr_retail_price"):
        row.custom_fbr_retail_price = calculated[
            "retail_price"
        ]


def build_item_payload(row, settings, invoice_type):
    if cint(invoice_type) == 2:
        return build_return_item_payload(row)

    config = get_item_fbr_configuration(
        row.item_code
    )

    calculated = calculate_fbr_item(
        row,
        config,
    )

    hs_code = get_item_hs_code(
        row,
        settings,
    )

    update_row_fbr_snapshot(
        row,
        config,
        calculated,
        hs_code,
    )

    discount = get_discount_amount(row)

    sale_value = money(
        calculated["value_excluding_tax"]
    )

    tax_charged = money(
        calculated["sales_tax"]
    )

    total_amount = money(
        sale_value + tax_charged
    )

    return {
        "ItemCode": row.item_code or "",
        "ItemName": row.item_name or config["item_name"] or "",
        "PCTCode": hs_code,
        "Quantity": int(abs(flt(calculated["quantity"]))),
        "TaxRate": int(flt(config["tax_rate"])),
        "SaleValue": sale_value,
        "TaxCharged": tax_charged,
        "TotalAmount": total_amount,
        "Discount": discount,
        "FurtherTax": 0,
        "InvoiceType": 1,
    }


def _get_original_return_row(row):
    original_row_name = getattr(row, "pos_invoice_item", None)

    if not original_row_name:
        frappe.throw(
            _("Original POS Invoice Item reference is required for return item {0}").format(
                frappe.bold(row.item_code)
            )
        )

    return frappe.get_doc("POS Invoice Item", original_row_name)


def _snapshot_value(row, fieldname, default=0):
    return getattr(row, fieldname, None) if row.meta.has_field(fieldname) else default


def build_return_item_payload(row):
    """Build FBR credit-note item from original submitted row snapshot."""
    original = _get_original_return_row(row)

    qty = abs(flt(row.qty))
    original_qty = abs(flt(original.qty))

    if qty <= 0 or original_qty <= 0:
        frappe.throw(
            _("Quantity must be greater than zero for return item {0}").format(
                frappe.bold(row.item_code)
            )
        )

    ratio = qty / original_qty

    sale_value = money(flt(_snapshot_value(original, "custom_fbr_value_excluding_tax")) * ratio)
    tax_charged = money(flt(_snapshot_value(original, "custom_fbr_sales_tax")) * ratio)
    total_amount = money(sale_value + tax_charged)
    retail_price = money(flt(_snapshot_value(original, "custom_fbr_retail_price")) * ratio)

    update_row_fbr_snapshot(
        row,
        {
            "tax_category": _snapshot_value(original, "custom_fbr_tax_category", ""),
            "fbr_sale_type": _snapshot_value(original, "custom_fbr_sale_type", ""),
            "tax_rate": flt(_snapshot_value(original, "custom_fbr_tax_rate")),
            "is_third_schedule": cint(_snapshot_value(original, "custom_fbr_is_third_schedule")),
            "mrp": flt(_snapshot_value(original, "custom_fbr_mrp")),
        },
        {
            "value_excluding_tax": sale_value,
            "sales_tax": tax_charged,
            "retail_price": retail_price,
        },
        _snapshot_value(original, "custom_fbr_hs_code", ""),
    )

    return {
        "ItemCode": row.item_code or "",
        "ItemName": row.item_name or original.item_name or "",
        "PCTCode": _snapshot_value(original, "custom_fbr_hs_code", "") or DEFAULT_HS_CODE,
        "Quantity": int(qty),
        "TaxRate": int(flt(_snapshot_value(original, "custom_fbr_tax_rate"))),
        "SaleValue": sale_value,
        "TaxCharged": tax_charged,
        "TotalAmount": total_amount,
        "Discount": money(abs(flt(original.discount_amount)) * ratio),
        "FurtherTax": 0,
        "InvoiceType": 2,
    }


def get_posting_datetime(doc):
    posting_time = (
        getattr(doc, "posting_time", None)
        or "00:00:00"
    )

    return get_datetime(
        f"{doc.posting_date} {posting_time}"
    )


def get_usin(doc):
    """
    Use ERPNext POS Invoice name as USIN.
    """

    return doc.name


def build_pos_payload(doc):
    settings = get_settings_for_invoice(doc)
    customer = get_customer_snapshot(doc)

    is_return = cint(getattr(doc, "is_return", 0))

    if is_return:
        invoice_type = 2
        usin = get_usin(doc)
        original = (
            frappe.get_doc("POS Invoice", doc.return_against)
            if getattr(doc, "return_against", None)
            else None
        )
        ref_usin = (
            getattr(original, "custom_fbr_invoice_number", None)
            if original
            else None
        ) or getattr(doc, "return_against", None)

        if not ref_usin:
            frappe.throw(_("Original invoice reference is required for FBR credit note"))
    else:
        invoice_type = 1
        usin = get_usin(doc)
        ref_usin = usin

    payload_items = []

    total_sale_value = 0
    total_quantity = 0
    total_tax_charged = 0
    total_discount = 0

    for row in doc.items:
        if not row.item_code:
            continue

        item_payload = build_item_payload(
            row,
            settings,
            invoice_type,
        )

        payload_items.append(item_payload)

        total_sale_value += flt(item_payload["SaleValue"])
        total_quantity += flt(item_payload["Quantity"])
        total_tax_charged += flt(item_payload["TaxCharged"])
        total_discount += flt(item_payload["Discount"])

    if not payload_items:
        frappe.throw(
            _("No valid items found for FBR payload")
        )

    total_sale_value = money(total_sale_value)
    total_tax_charged = money(total_tax_charged)
    total_discount = money(total_discount)

    total_bill_amount = money(
        total_sale_value
        + total_tax_charged
        - total_discount
    )

    posting_datetime = get_posting_datetime(doc)

    payload = {
        "FBRInvoiceNumber": None,
        "POSID": int(settings["pos_id"]),
        "USIN": usin,
        "BuyerName": customer["name"],
        "BuyerNTN": customer["ntn"],
        "BuyerCNIC": customer["cnic"],
        "BuyerPhoneNumber": customer["phone"] or "",
        "DateTime": posting_datetime.strftime(
            "%Y-%m-%d %H:%M:%S.000"
        ),
        "TotalSaleValue": f"{total_sale_value:.2f}",
        "TotalQuantity": str(int(total_quantity)),
        "TotalTaxCharged": f"{total_tax_charged:.2f}",
        "Discount": f"{total_discount:.2f}",
        "TotalBillAmount": f"{total_bill_amount:.2f}",
        "PaymentMode": get_payment_mode(doc, settings),
        "InvoiceType": invoice_type,
        "RefUSIN": ref_usin,
        "Items": payload_items,
    }

    return payload
