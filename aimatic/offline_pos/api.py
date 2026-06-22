import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate, nowtime

_MAX_PAGE_SIZE = 1000


# ---------------------------------------------------------------------------
# Internal helpers — auth, validation, document loading
# ---------------------------------------------------------------------------

def _require_login():
    if frappe.session.user == "Guest":
        frappe.throw(_("Login required"), frappe.PermissionError)


def _load_pos_profile(pos_profile_name):
    """Return the POS Profile doc after permission and user-membership checks."""
    try:
        pos = frappe.get_cached_doc("POS Profile", pos_profile_name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Invalid POS Profile: {0}").format(pos_profile_name))

    try:
        pos.check_permission("read")
    except frappe.PermissionError:
        frappe.throw(_("Not permitted to read POS Profile: {0}").format(pos_profile_name))

    user = frappe.session.user
    user_list = [r.user for r in (pos.get("applicable_for_users") or [])]
    if user_list and user not in user_list:
        frappe.throw(
            _("POS Profile {0} is not available for user {1}").format(pos_profile_name, user),
            frappe.PermissionError,
        )

    return pos


def _validate_pos_opening_entry(pos_profile_name):
    """Raise if there is no active submitted POS Opening Entry for the current user and profile."""
    opening_entries = frappe.get_all(
        "POS Opening Entry",
        fields=["name", "period_start_date"],
        filters={
            "pos_profile": pos_profile_name,
            "user": frappe.session.user,
            "status": "Open",
            "docstatus": 1,
        },
        order_by="period_start_date desc",
        limit=2,
    )

    if not opening_entries:
        frappe.throw(
            _("No open POS Opening Entry found for POS Profile {0}.").format(pos_profile_name),
            title=_("POS Opening Entry Missing"),
        )

    return opening_entries[0]


def _load_customer(customer_name):
    """Return the Customer doc after permission check."""
    try:
        cust = frappe.get_cached_doc("Customer", customer_name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Invalid Customer: {0}").format(customer_name))

    try:
        cust.check_permission("read")
    except frappe.PermissionError:
        frappe.throw(_("Not permitted to read Customer: {0}").format(customer_name))

    return cust


def _parse_json_param(value, name="parameter"):
    """Coerce a string to a Python object; pass through lists/dicts unchanged."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            frappe.throw(_("Invalid JSON for {0}").format(name))
    return value


# ---------------------------------------------------------------------------
# Shared invoice builder
# ---------------------------------------------------------------------------

def _build_pos_invoice_doc(
    pos,
    cust,
    items,
    coupon_code=None,
    redeem_loyalty_points=0,
    loyalty_points=0,
):
    """Build and price an unsaved POS Invoice through the full ERPNext pipeline.

    Runs set_missing_values, calculate_taxes_and_totals, pricing rule application,
    coupon validation, and loyalty-points validation.

    Does NOT call FBR functions — callers decide when to apply the FBR payload
    builder and accounting rows.  This keeps preview and submit behaviour
    consistent without duplicating logic.
    """
    items = _parse_json_param(items, "items")
    if not isinstance(items, list) or not items:
        frappe.throw(_("items must be a non-empty list"))

    # Validate all quantities before touching ERPNext pricing
    for it in items:
        try:
            qty = float(it.get("qty") or it.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:
            frappe.throw(
                _("Quantity must be greater than zero for item {0}").format(
                    it.get("item_code") or it.get("barcode") or "?"
                )
            )

    doc = frappe.get_doc({
        "doctype": "POS Invoice",
        "is_pos": 1,
        "pos_profile": pos.name,
        "customer": cust.name,
        "company": pos.company or frappe.defaults.get_user_default("Company"),
        "posting_date": nowdate(),
        "posting_time": nowtime(),
        "set_posting_time": 1,
    })

    frappe.flags.pos_profile = pos

    if coupon_code:
        doc.coupon_code = coupon_code

    for it in items:
        item_code = it.get("item_code")
        barcode = it.get("barcode")
        qty = float(it.get("qty") or it.get("quantity") or 0)
        uom = it.get("uom")

        if not item_code and barcode:
            item_code = frappe.db.get_value("Item Barcode", {"barcode": barcode}, "parent")

        if not item_code:
            frappe.throw(
                _("Item code not provided and barcode not found: {0}").format(barcode)
            )

        item_flags = frappe.db.get_value(
            "Item", item_code, ["disabled", "is_sales_item"], as_dict=True
        )
        if not item_flags:
            frappe.throw(_("Item not found: {0}").format(item_code))
        if item_flags.disabled:
            frappe.throw(_("Item {0} is disabled").format(item_code))
        if not item_flags.is_sales_item:
            frappe.throw(_("Item {0} is not a sales item").format(item_code))

        if not uom:
            uom = frappe.get_cached_value("Item", item_code, "stock_uom")

        doc.append("items", {
            "doctype": "POS Invoice Item",
            "item_code": item_code,
            "qty": qty,
            "uom": uom,
        })

    from erpnext.accounts.doctype.pricing_rule.utils import (
        apply_pricing_rule_on_transaction,
        validate_coupon_code,
    )

    doc.run_method("set_missing_values", True)
    doc.run_method("calculate_taxes_and_totals")

    if coupon_code:
        validate_coupon_code(coupon_code)

    apply_pricing_rule_on_transaction(doc)

    if int(redeem_loyalty_points or 0):
        from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
            validate_loyalty_points,
        )

        try:
            points = int(loyalty_points or 0)
        except (TypeError, ValueError):
            points = 0

        if points > 0:
            doc.redeem_loyalty_points = 1
            doc.loyalty_points = points
            validate_loyalty_points(doc, points)

    return doc


# ---------------------------------------------------------------------------
# Public API endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_item_barcodes(limit_start=0, limit_page_length=500):
    """Return active sales item barcodes with correct pre-filtered pagination.

    Response keys: rows, next_start, has_more
    """
    _require_login()

    try:
        start = int(limit_start or 0)
        page_length = min(int(limit_page_length or 500), _MAX_PAGE_SIZE)
    except Exception:
        start = 0
        page_length = 500

    # Filter inactive items and non-sales items at the database level so that
    # pagination counts are always accurate.
    rows = frappe.db.sql(
        """
        SELECT
            `tabItem Barcode`.parent  AS item_code,
            `tabItem Barcode`.barcode,
            `tabItem Barcode`.uom,
            `tabItem Barcode`.modified
        FROM `tabItem Barcode`
        INNER JOIN `tabItem`
            ON  `tabItem`.name = `tabItem Barcode`.parent
        WHERE `tabItem Barcode`.barcode != ''
          AND `tabItem`.disabled = 0
          AND `tabItem`.is_sales_item = 1
        ORDER BY `tabItem Barcode`.modified DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"limit": page_length + 1, "offset": start},
        as_dict=True,
    )

    has_more = len(rows) > page_length
    rows = rows[:page_length]
    next_start = start + len(rows) if has_more else None

    return {"rows": rows, "next_start": next_start, "has_more": has_more}


@frappe.whitelist()
def preview_cart(
    pos_profile,
    customer,
    items,
    coupon_code=None,
    redeem_loyalty_points=0,
    loyalty_points=0,
):
    """Build an unsaved POS Invoice and return priced/taxed cart preview.

    Never inserts, saves, submits, calls FBR servers, updates coupon counts,
    or creates loyalty ledger entries.
    """
    _require_login()

    pos = _load_pos_profile(pos_profile)
    _validate_pos_opening_entry(pos.name)
    cust = _load_customer(customer)

    items = _parse_json_param(items, "items")
    if not items:
        return {"rows": [], "taxes": [], "totals": {}}

    doc = _build_pos_invoice_doc(
        pos, cust, items, coupon_code, redeem_loyalty_points, loyalty_points
    )

    from aimatic.fbr_pos.accounting import apply_fbr_accounting_rows
    from aimatic.fbr_pos.payload_builder import build_pos_payload
    from erpnext.accounts.doctype.pricing_rule.utils import get_applied_pricing_rules

    build_pos_payload(doc)
    apply_fbr_accounting_rows(doc)
    doc.run_method("calculate_taxes_and_totals")

    rows = []
    for itm in doc.get("items"):
        try:
            applied_rules = get_applied_pricing_rules(getattr(itm, "pricing_rules", None)) or []
        except Exception:
            applied_rules = []

        rows.append({
            "item_code": itm.item_code,
            "item_name": getattr(itm, "item_name", ""),
            "uom": getattr(itm, "uom", None),
            "quantity": getattr(itm, "qty", 0),
            "price_list_rate": getattr(itm, "price_list_rate", None),
            "rate": getattr(itm, "rate", None),
            "discount_percentage": getattr(itm, "discount_percentage", None),
            "discount_amount": getattr(itm, "discount_amount", None),
            "net_rate": getattr(itm, "net_rate", None) or getattr(itm, "rate", None),
            # line_amount = inclusive customer selling price (rate × qty).
            # Never use net_amount — it drops after ERPNext inclusive tax maths.
            "line_amount": flt(getattr(itm, "amount", None) or 0, 2),
            "item_tax_template": getattr(itm, "item_tax_template", None),
            "applied_pricing_rules": applied_rules,
            # FBR snapshot fields (set by build_pos_payload → update_row_fbr_snapshot)
            "custom_fbr_tax_category": getattr(itm, "custom_fbr_tax_category", None),
            "custom_fbr_sale_type": getattr(itm, "custom_fbr_sale_type", None),
            "custom_fbr_tax_rate": getattr(itm, "custom_fbr_tax_rate", None),
            "custom_fbr_is_third_schedule": getattr(itm, "custom_fbr_is_third_schedule", None),
            "custom_fbr_mrp": getattr(itm, "custom_fbr_mrp", None),
            "custom_fbr_value_excluding_tax": getattr(itm, "custom_fbr_value_excluding_tax", None),
            "custom_fbr_sales_tax": getattr(itm, "custom_fbr_sales_tax", None),
            "custom_fbr_retail_price": getattr(itm, "custom_fbr_retail_price", None),
            "custom_fbr_hs_code": getattr(itm, "custom_fbr_hs_code", None),
        })

    taxes = [
        {
            "account_head": getattr(t, "account_head", None),
            "description": getattr(t, "description", None),
            "charge_type": getattr(t, "charge_type", None),
            "rate": getattr(t, "rate", None),
            "tax_amount": getattr(t, "tax_amount", None) or getattr(t, "amount", None),
            "total": getattr(t, "total", None),
            "included_in_print_rate": getattr(t, "included_in_print_rate", None),
        }
        for t in (doc.get("taxes") or [])
    ]

    # FBR invoice totals derived from item snapshot rows
    merchandise_total = flt(sum(flt(r.get("line_amount") or 0) for r in rows), 2)
    value_excluding_tax = flt(
        sum(flt(r.get("custom_fbr_value_excluding_tax") or 0) for r in rows), 2
    )
    total_sales_tax = flt(
        sum(flt(r.get("custom_fbr_sales_tax") or 0) for r in rows), 2
    )
    fbr_pos_service_fee = 0.0
    for t in (doc.get("taxes") or []):
        if (getattr(t, "description", "") or "").strip() == "FBR POS Service Fee":
            fbr_pos_service_fee = flt(
                getattr(t, "tax_amount", 0) or getattr(t, "amount", 0), 2
            )
            break

    totals = {
        "total": getattr(doc, "total", None),
        "net_total": getattr(doc, "net_total", None),
        "total_taxes_and_charges": getattr(doc, "total_taxes_and_charges", None),
        "grand_total": getattr(doc, "grand_total", None),
        "rounding_adjustment": getattr(doc, "rounding_adjustment", None),
        "rounded_total": getattr(doc, "rounded_total", None),
        "loyalty_amount": getattr(doc, "loyalty_amount", None),
        "currency": getattr(doc, "currency", None) or getattr(doc, "company_currency", None),
        # Authoritative FBR totals — Electron must use these, not its own calculations
        "merchandise_total": merchandise_total,
        "value_excluding_tax": value_excluding_tax,
        "total_sales_tax": total_sales_tax,
        "fbr_pos_service_fee": fbr_pos_service_fee,
    }

    return {"rows": rows, "taxes": taxes, "totals": totals}


@frappe.whitelist()
def submit_online_sale(
    terminal_invoice_id,
    terminal_id,
    pos_profile,
    opening_entry,
    customer,
    items,
    payments,
    coupon_code=None,
    redeem_loyalty_points=0,
    loyalty_points=0,
):
    """Create and submit a POS Invoice from the Electron terminal.

    Idempotent: if a POS Invoice with the same terminal_invoice_id already exists
    it is returned immediately without creating a duplicate.  This protects
    against Electron losing the HTTP response after the invoice was created.

    All totals, pricing rules, taxes and payable amounts are recalculated on the
    server.  Values sent by Electron are never trusted.

    FBR snapshot and accounting rows are applied by the existing validate hook
    (validate_pos_invoice) during doc.insert().  The FBR API call happens through
    the existing before_submit hook (before_submit_pos_invoice) during doc.submit().
    Neither hook is called manually here.
    """
    _require_login()

    if not frappe.has_permission("POS Invoice", "create"):
        frappe.throw(_("Not permitted to create POS Invoice"), frappe.PermissionError)

    if not terminal_invoice_id:
        frappe.throw(_("terminal_invoice_id is required"))

    # Idempotency: return existing invoice if one was already created for this terminal request
    existing_name = _find_existing_invoice(terminal_invoice_id)
    if existing_name:
        return _build_submission_response(frappe.get_doc("POS Invoice", existing_name))

    # Auth and profile checks
    pos = _load_pos_profile(pos_profile)
    _validate_pos_opening_entry(pos.name)
    cust = _load_customer(customer)

    # Coerce JSON-string parameters that arrive as strings over HTTP
    items = _parse_json_param(items, "items")
    payments = _parse_json_param(payments, "payments")

    # Build and price the invoice through the full ERPNext pipeline (no FBR yet)
    doc = _build_pos_invoice_doc(
        pos, cust, items, coupon_code, redeem_loyalty_points, loyalty_points
    )

    # Apply FBR snapshot + accounting rows to obtain the true grand_total that
    # includes FBR sales tax and the FBR POS service fee.  No payment rows are
    # set on the doc at this point, so adjust_cash_payment_to_grand_total is a
    # no-op and does not interfere.
    from aimatic.fbr_pos.accounting import apply_fbr_accounting_rows
    from aimatic.fbr_pos.payload_builder import build_pos_payload

    build_pos_payload(doc)
    apply_fbr_accounting_rows(doc)

    # Validate the client-supplied payments against the server-computed grand_total
    # and write them onto the doc.
    _validate_and_set_payments(doc, pos, payments)

    # Stamp terminal identifiers before insert so they are stored with the record
    _set_terminal_fields(doc, terminal_invoice_id, terminal_id)

    # Insert + submit within a named savepoint so any unexpected failure rolls back
    # cleanly without aborting the outer transaction.
    sp = "submit_online_sale"
    frappe.db.savepoint(sp)
    try:
        doc.insert()   # validate hook fires: FBR snapshot + accounting rows (idempotent)
        doc.submit()   # before_submit hook fires: FBR API call
        frappe.db.release_savepoint(sp)
    except frappe.UniqueValidationError:
        frappe.db.rollback(save_point=sp)
        # Race condition: a concurrent request created the same invoice first
        existing_name = _find_existing_invoice(terminal_invoice_id)
        if existing_name:
            return _build_submission_response(frappe.get_doc("POS Invoice", existing_name))
        raise
    except Exception:
        frappe.db.rollback(save_point=sp)
        raise

    return _build_submission_response(doc)


# ---------------------------------------------------------------------------
# submit_online_sale helpers
# ---------------------------------------------------------------------------

def _find_existing_invoice(terminal_invoice_id):
    """Return the name of an existing POS Invoice with this terminal_invoice_id, or None."""
    if not frappe.get_meta("POS Invoice").has_field("custom_terminal_invoice_id"):
        return None
    return frappe.db.get_value(
        "POS Invoice",
        {"custom_terminal_invoice_id": terminal_invoice_id},
        "name",
    )


def _validate_and_set_payments(doc, pos, payments_data):
    """Validate client payment rows and write them onto the doc.

    Rules enforced:
    - Every mode_of_payment must be in the POS Profile's allowed list.
    - Every amount must be positive.
    - Non-cash payments cannot individually exceed the remaining payable amount.
    - Cash may exceed payable (creates change).
    - Total payments must cover payable_after_loyalty.
    """
    if not isinstance(payments_data, list) or not payments_data:
        frappe.throw(_("At least one payment row is required"))

    allowed_modes = {p.mode_of_payment for p in (pos.get("payments") or [])}
    if not allowed_modes:
        frappe.throw(_("POS Profile has no payment modes configured"))

    _mop_type_cache = {}

    def _is_cash(mode):
        if mode not in _mop_type_cache:
            _mop_type_cache[mode] = (
                frappe.db.get_value("Mode of Payment", mode, "type") or "General"
            )
        return _mop_type_cache[mode] == "Cash"

    grand_total = flt(doc.grand_total, 2)
    loyalty_amount = flt(getattr(doc, "loyalty_amount", 0) or 0, 2)
    payable = flt(grand_total - loyalty_amount, 2)

    total_paid = 0.0

    for p in payments_data:
        mode = (p.get("mode_of_payment") or "").strip()
        amount = flt(p.get("amount") or 0, 2)

        if not mode:
            frappe.throw(_("Each payment row must have mode_of_payment"))

        if mode not in allowed_modes:
            frappe.throw(
                _("Payment mode '{0}' is not allowed in POS Profile {1}").format(
                    mode, pos.name
                )
            )

        if amount <= 0:
            frappe.throw(
                _("Payment amount must be greater than zero (mode: {0})").format(mode)
            )

        if not _is_cash(mode):
            remaining = flt(payable - total_paid, 2)
            if amount > flt(remaining + 0.005, 2):
                frappe.throw(
                    _(
                        "Non-cash payment '{0}' of {1} exceeds remaining payable {2}"
                    ).format(mode, amount, remaining)
                )

        total_paid = flt(total_paid + amount, 2)

    if total_paid < flt(payable - 0.005, 2):
        frappe.throw(
            _("Total payments {0} are less than the payable amount {1}").format(
                total_paid, payable
            )
        )

    doc.set("payments", [])
    for p in payments_data:
        mode = (p.get("mode_of_payment") or "").strip()
        amount = flt(p.get("amount") or 0, 2)
        if mode and amount > 0:
            doc.append("payments", {"mode_of_payment": mode, "amount": amount})


def _set_terminal_fields(doc, terminal_invoice_id, terminal_id):
    meta = doc.meta
    if meta.has_field("custom_terminal_invoice_id"):
        doc.custom_terminal_invoice_id = terminal_invoice_id
    if meta.has_field("custom_terminal_id"):
        doc.custom_terminal_id = terminal_id or ""


def _build_submission_response(doc):
    """Serialize a submitted (or existing) POS Invoice into the API response dict."""
    meta = doc.meta

    def _cf(fieldname):
        return getattr(doc, fieldname, None) if meta.has_field(fieldname) else None

    # Attempt to extract a QR URL from the FBR JSON response payload
    fbr_qr = None
    fbr_response_json = _cf("custom_fbr_response_payload")
    if fbr_response_json:
        try:
            resp = json.loads(fbr_response_json)
            fbr_qr = (
                resp.get("QRUrl")
                or resp.get("QrUrl")
                or resp.get("qr_url")
                or resp.get("QRCode")
                or resp.get("qrCode")
            )
        except Exception:
            pass

    payments_out = [
        {"mode_of_payment": p.mode_of_payment, "amount": flt(p.amount, 2)}
        for p in (doc.get("payments") or [])
    ]

    return {
        "terminal_invoice_id": _cf("custom_terminal_invoice_id"),
        "pos_invoice": doc.name,
        "docstatus": doc.docstatus,
        "customer": doc.customer,
        "currency": getattr(doc, "currency", None),
        "total": flt(getattr(doc, "total", 0) or 0, 2),
        "net_total": flt(getattr(doc, "net_total", 0) or 0, 2),
        "total_taxes_and_charges": flt(
            getattr(doc, "total_taxes_and_charges", 0) or 0, 2
        ),
        "grand_total": flt(getattr(doc, "grand_total", 0) or 0, 2),
        "rounded_total": flt(
            getattr(doc, "rounded_total", None) or getattr(doc, "grand_total", 0) or 0,
            2,
        ),
        "loyalty_amount": flt(getattr(doc, "loyalty_amount", 0) or 0, 2),
        "coupon_code": getattr(doc, "coupon_code", None),
        "payments": payments_out,
        "change_amount": flt(getattr(doc, "change_amount", 0) or 0, 2),
        "fbr_status": _cf("custom_fbr_status"),
        "fbr_invoice_number": _cf("custom_fbr_invoice_number"),
        "fbr_qr": fbr_qr,
        "fbr_usin": _cf("custom_fbr_usin"),
    }


@frappe.whitelist()
def get_customer_benefits(pos_profile, customer):
    """Return loyalty program details for a customer using the POS Profile company.

    Returns zero/None values when the customer is not enrolled.
    """
    _require_login()

    pos = _load_pos_profile(pos_profile)
    cust = _load_customer(customer)

    company = pos.company or frappe.get_default("company")

    loyalty_program = frappe.db.get_value("Customer", cust.name, "loyalty_program")
    if not loyalty_program:
        return {
            "loyalty_program": None,
            "available_loyalty_points": 0,
            "conversion_factor": None,
            "loyalty_value": 0,
            "expense_account": None,
            "cost_center": None,
        }

    from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
        get_loyalty_program_details_with_points,
    )

    details = get_loyalty_program_details_with_points(
        cust.name, loyalty_program, company=company
    )

    points = int(details.get("loyalty_points") or 0)
    conv = float(details.get("conversion_factor") or 0)

    return {
        "loyalty_program": details.get("loyalty_program"),
        "available_loyalty_points": points,
        "conversion_factor": conv,
        "loyalty_value": points * conv,
        "expense_account": details.get("expense_account"),
        "cost_center": details.get("cost_center"),
    }


# ---------------------------------------------------------------------------
# POS session management
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_active_pos_session(pos_profile):
    """Return diagnostic info about the current POS session state.

    Always returns a dict with keys:
        authenticated_user, requested_pos_profile, session, open_entries, reason
    """
    _require_login()

    user = frappe.session.user

    # All submitted open entries across all users/profiles — for diagnostics
    all_open = frappe.get_all(
        "POS Opening Entry",
        filters={"docstatus": 1, "status": "Open"},
        fields=["name", "user", "pos_profile", "status", "docstatus", "period_start_date"],
        order_by="period_start_date desc",
    )

    if not pos_profile:
        return {
            "authenticated_user": user,
            "requested_pos_profile": pos_profile,
            "session": None,
            "open_entries": all_open,
            "reason": "POS Profile argument is empty",
        }

    # Exact match for the authenticated user + requested profile
    matched = frappe.get_all(
        "POS Opening Entry",
        filters={
            "pos_profile": pos_profile,
            "user": user,
            "docstatus": 1,
            "status": "Open",
        },
        fields=["name", "period_start_date"],
        order_by="period_start_date desc",
        limit=1,
    )

    if matched:
        session_doc = frappe.get_doc("POS Opening Entry", matched[0].name).as_dict()
        return {
            "authenticated_user": user,
            "requested_pos_profile": pos_profile,
            "session": session_doc,
            "open_entries": all_open,
            "reason": "Active session found",
        }

    # Determine a specific reason for the miss
    profile_entries = [e for e in all_open if e.pos_profile == pos_profile]
    user_entries = [e for e in all_open if e.user == user]

    if not all_open:
        reason = "No submitted open POS Opening Entry exists"
    elif profile_entries and not any(e.user == user for e in profile_entries):
        reason = "Open session exists but cashier does not match authenticated API user"
    elif user_entries and not any(e.pos_profile == pos_profile for e in user_entries):
        reason = "Open session exists but POS Profile does not match"
    else:
        reason = "No submitted open POS Opening Entry exists"

    return {
        "authenticated_user": user,
        "requested_pos_profile": pos_profile,
        "session": None,
        "open_entries": all_open,
        "reason": reason,
    }


@frappe.whitelist()
def start_pos_session(pos_profile, opening_balances=None):
    """Create and submit a POS Opening Entry for the current user.

    If an open session already exists for this user + profile, returns it
    instead of creating a duplicate.

    opening_balances: JSON string or list of {mode_of_payment, opening_amount}.
                      Electron should send this JSON-stringified.
    """
    _require_login()

    if not frappe.has_permission("POS Opening Entry", "create"):
        frappe.throw(_("Not permitted to create POS Opening Entry"), frappe.PermissionError)
    if not frappe.has_permission("POS Opening Entry", "submit"):
        frappe.throw(_("Not permitted to submit POS Opening Entry"), frappe.PermissionError)

    pos = _load_pos_profile(pos_profile)

    if isinstance(opening_balances, str):
        opening_balances = frappe.parse_json(opening_balances)
    if opening_balances is not None and not isinstance(opening_balances, list):
        frappe.throw(_("opening_balances must be a list"))
    opening_balances = opening_balances or []

    profile_payments = pos.get("payments") or []
    allowed_modes = {p.mode_of_payment for p in profile_payments}

    if not allowed_modes:
        frappe.throw(
            _("No payment methods are configured in POS Profile: {0}").format(pos.name)
        )

    if not opening_balances:
        # Default: one zero-amount row for every payment mode in the profile
        balance_rows = [
            {"mode_of_payment": p.mode_of_payment, "opening_amount": 0.0}
            for p in profile_payments
        ]
    else:
        balance_rows = []
        for row in opening_balances:
            mode = (row.get("mode_of_payment") or "").strip()
            if not mode:
                frappe.throw(_("Each opening balance row must have mode_of_payment"))
            if mode not in allowed_modes:
                frappe.throw(
                    _("Payment mode '{0}' is not configured in POS Profile {1}").format(
                        mode, pos.name
                    )
                )
            amount = flt(row.get("opening_amount") or 0)
            if amount < 0:
                frappe.throw(
                    _("Opening amount cannot be negative for payment mode '{0}'").format(mode)
                )
            balance_rows.append({"mode_of_payment": mode, "opening_amount": amount})

    # Idempotency: return existing open session if one already exists
    existing = frappe.get_all(
        "POS Opening Entry",
        filters={
            "pos_profile": pos.name,
            "user": frappe.session.user,
            "docstatus": 1,
            "status": "Open",
        },
        fields=["name"],
        order_by="period_start_date desc",
        limit=1,
    )
    if existing:
        return frappe.get_doc("POS Opening Entry", existing[0].name).as_dict()

    entry = frappe.new_doc("POS Opening Entry")
    entry.pos_profile = pos.name
    entry.user = frappe.session.user
    entry.company = pos.company
    entry.posting_date = nowdate()
    entry.period_start_date = now_datetime()

    for row in balance_rows:
        entry.append("balance_details", {
            "mode_of_payment": row["mode_of_payment"],
            "opening_amount": row["opening_amount"],
        })

    if not entry.balance_details:
        frappe.throw(
            _("No payment methods are configured in POS Profile: {0}").format(pos.name)
        )

    entry.insert()
    entry.submit()

    return entry.as_dict()


# ---------------------------------------------------------------------------
# FBR item configuration for Electron
# ---------------------------------------------------------------------------

def _get_fbr_service_fee():
    """Return the FBR POS service fee amount from any enabled FBR Integration Settings.

    Reads the first enabled record that has enable_fbr_for_pos=1 so the caller
    does not need a company or branch context.  Returns 0 when no settings exist.
    """
    settings_name = frappe.db.get_value(
        "FBR Integration Settings",
        {"enabled": 1, "enable_fbr_for_pos": 1},
        "name",
    )
    if not settings_name:
        return 0.0
    return flt(
        frappe.db.get_value(
            "FBR Integration Settings", settings_name, "fbr_pos_fee_amount"
        ) or 0,
        2,
    )


@frappe.whitelist()
def get_pos_fbr_item_config(
    item_codes=None,
    modified_after=None,
    limit_start=0,
    limit_page_length=500,
):
    """Return FBR tax configuration for active sales items.

    Supports incremental sync via modified_after (ISO datetime string) and
    targeted lookup via item_codes (JSON array or Python list).

    Response:
        rows        — list of item + FBR Tax Category fields
        next_start  — offset for the next page, or null
        has_more    — boolean
        service_fee — FBR POS service fee from FBR Integration Settings
    """
    _require_login()

    if isinstance(item_codes, str):
        item_codes = frappe.parse_json(item_codes)

    try:
        start = int(limit_start or 0)
        page_length = min(int(limit_page_length or 500), _MAX_PAGE_SIZE)
    except Exception:
        start = 0
        page_length = 500

    filters = {"disabled": 0, "is_sales_item": 1}
    if modified_after:
        filters["modified"] = (">", modified_after)
    if item_codes and isinstance(item_codes, list) and item_codes:
        filters["item_code"] = ("in", item_codes)

    # Fetch one extra row to determine whether a next page exists
    items = frappe.get_all(
        "Item",
        filters=filters,
        fields=[
            "item_code",
            "item_name",
            "modified",
            "custom_fbr_tax_category",
            "custom_fbr_hs_code",
            "custom_mrp",
            "custom_is_3rd_schedule",
        ],
        order_by="modified desc",
        limit_start=start,
        limit_page_length=page_length + 1,
    )

    has_more = len(items) > page_length
    items = items[:page_length]
    next_start = start + len(items) if has_more else None

    # Preload all referenced FBR Tax Categories in a single query
    cat_names = list({it.custom_fbr_tax_category for it in items if it.custom_fbr_tax_category})
    cat_map = {}
    if cat_names:
        cats = frappe.get_all(
            "FBR Tax Category",
            filters={"name": ("in", cat_names)},
            fields=[
                "name",
                "tax_rate",
                "is_third_schedule",
                "is_exempt",
                "is_zero_rated",
                "fbr_sale_type",
                "enabled",
            ],
        )
        cat_map = {c.name: c for c in cats}

    rows = []
    for it in items:
        cat = cat_map.get(it.custom_fbr_tax_category) if it.custom_fbr_tax_category else None
        rows.append({
            "item_code": it.item_code,
            "item_name": it.item_name,
            "modified": str(it.modified),
            "custom_fbr_tax_category": it.custom_fbr_tax_category,
            "custom_fbr_hs_code": it.custom_fbr_hs_code,
            "custom_mrp": flt(it.custom_mrp, 2),
            "custom_is_3rd_schedule": cint(it.custom_is_3rd_schedule),
            # FBR Tax Category fields — null when no category is linked
            "tax_rate": flt(cat.tax_rate, 2) if cat else None,
            "is_third_schedule": cint(cat.is_third_schedule) if cat else None,
            "is_exempt": cint(cat.is_exempt) if cat else None,
            "is_zero_rated": cint(cat.is_zero_rated) if cat else None,
            "fbr_sale_type": cat.fbr_sale_type if cat else None,
            "enabled": cint(cat.enabled) if cat else None,
        })

    return {
        "rows": rows,
        "next_start": next_start,
        "has_more": has_more,
        "service_fee": _get_fbr_service_fee(),
    }
