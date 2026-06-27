import json
import secrets

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime, nowdate, nowtime
from frappe.utils.data import sha256_hash
from frappe.utils.password import check_password

_MAX_PAGE_SIZE = 1000
_ALLOWED_POS_ADMIN_ACTIONS = {"setup_pin", "reset_pin", "change_credentials"}
_ALLOWED_POS_ADMIN_ROLES = {"POS Supervisor", "System Manager"}
_ALLOWED_REFUND_ROLES = {"POS Supervisor", "System Manager"}


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


def _hash_pos_admin_token(token):
    return sha256_hash(token or "")


def _audit_pos_admin_action(user, action, terminal_id, status):
    if user and not frappe.db.exists("User", user):
        user = None

    frappe.get_doc({
        "doctype": "POS Admin Audit Log",
        "user": user,
        "action": action,
        "terminal_id": terminal_id,
        "status": status,
        "created_at": now_datetime(),
    }).insert(ignore_permissions=True)


def _require_https_for_pos_admin_authorization():
    request = getattr(frappe.local, "request", None)
    scheme = (getattr(request, "scheme", "") or "").lower()

    if scheme != "https":
        frappe.throw(_("HTTPS is required for supervisor authorization"))


def _validate_pos_admin_action_args(action, terminal_id):
    if action not in _ALLOWED_POS_ADMIN_ACTIONS:
        frappe.throw(_("Invalid admin action"))

    if not terminal_id:
        frappe.throw(_("Supervisor credentials, action and terminal ID are required"))


@frappe.whitelist()
def authorize_pos_admin_action(username, password, action, terminal_id):
    _require_login()
    _require_https_for_pos_admin_authorization()

    _validate_pos_admin_action_args(action, terminal_id)

    if not username or not password:
        frappe.throw(_("Supervisor credentials, action and terminal ID are required"))

    # Use Frappe password verification. Do not compare hashes manually.
    try:
        check_password(username, password)
    except Exception:
        _audit_pos_admin_action(username, action, terminal_id, "failed")
        frappe.throw(_("Supervisor authorization failed"), frappe.AuthenticationError)

    user = frappe.get_doc("User", username)
    if not user.enabled:
        _audit_pos_admin_action(username, action, terminal_id, "disabled_user")
        frappe.throw(_("Supervisor authorization failed"), frappe.AuthenticationError)

    roles = set(frappe.get_roles(username))
    if not roles.intersection(_ALLOWED_POS_ADMIN_ROLES):
        _audit_pos_admin_action(username, action, terminal_id, "missing_role")
        frappe.throw(_("Supervisor authorization failed"), frappe.PermissionError)

    token = secrets.token_urlsafe(32)
    token_hash = _hash_pos_admin_token(token)
    expires_at = add_to_date(now_datetime(), minutes=5)

    doc = frappe.get_doc({
        "doctype": "POS Admin Authorization",
        "user": username,
        "action": action,
        "terminal_id": terminal_id,
        "token_hash": token_hash,
        "expires_at": expires_at,
        "used": 0,
    })
    doc.insert(ignore_permissions=True)

    _audit_pos_admin_action(username, action, terminal_id, "success")

    return {
        "token": token,
        "expires_at": str(expires_at),
    }


@frappe.whitelist()
def consume_pos_admin_authorization(token, action, terminal_id):
    _require_login()
    _require_https_for_pos_admin_authorization()

    _validate_pos_admin_action_args(action, terminal_id)

    if not token:
        frappe.throw(_("Supervisor authorization token is required"))

    token_hash = _hash_pos_admin_token(token)
    auth_name = frappe.db.get_value(
        "POS Admin Authorization",
        {
            "token_hash": token_hash,
            "action": action,
            "terminal_id": terminal_id,
        },
        "name",
    )

    if not auth_name:
        _audit_pos_admin_action(None, action, terminal_id, "invalid_token")
        frappe.throw(_("Supervisor authorization token is invalid"), frappe.PermissionError)

    frappe.db.sql(
        "SELECT name FROM `tabPOS Admin Authorization` WHERE name = %s FOR UPDATE",
        (auth_name,),
    )
    auth = frappe.get_doc("POS Admin Authorization", auth_name)

    if cint(auth.used):
        _audit_pos_admin_action(auth.user, action, terminal_id, "token_used")
        frappe.throw(_("Supervisor authorization token has already been used"), frappe.PermissionError)

    if now_datetime() > get_datetime(auth.expires_at):
        _audit_pos_admin_action(auth.user, action, terminal_id, "token_expired")
        frappe.throw(_("Supervisor authorization token has expired"), frappe.PermissionError)

    auth.used = 1
    auth.save(ignore_permissions=True)
    _audit_pos_admin_action(auth.user, action, terminal_id, "token_consumed")

    return {"success": True}


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
def get_item_barcodes(limit_start=0, limit_page_length=500, modified_after=None):
    """Return active sales item barcodes with correct pre-filtered pagination.

    Supports incremental sync via modified_after (ISO datetime string): when
    provided, only barcodes with `tabItem Barcode`.modified > modified_after
    are returned. Response keys unchanged: rows, next_start, has_more
    """
    _require_login()

    try:
        start = int(limit_start or 0)
        page_length = min(int(limit_page_length or 500), _MAX_PAGE_SIZE)
    except Exception:
        start = 0
        page_length = 500

    params = {"limit": page_length + 1, "offset": start}
    modified_clause = ""
    if modified_after:
        modified_clause = " AND `tabItem Barcode`.modified > %(modified_after)s"
        params["modified_after"] = modified_after

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
          {modified_clause}
        ORDER BY `tabItem Barcode`.modified DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """.format(modified_clause=modified_clause),
        params,
        as_dict=True,
    )

    has_more = len(rows) > page_length
    rows = rows[:page_length]
    next_start = start + len(rows) if has_more else None

    return {"rows": rows, "next_start": next_start, "has_more": has_more}


@frappe.whitelist()
def get_uom_conversions(limit_start=0, limit_page_length=500, modified_after=None):
    """Return UOM conversion factors for active sales items.

    Served via a custom method (not /api/resource/UOM Conversion Detail) because that child
    doctype is not directly readable by the POS user. Supports incremental sync via
    modified_after. Response keys: rows, next_start, has_more
    """
    _require_login()

    try:
        start = int(limit_start or 0)
        page_length = min(int(limit_page_length or 500), _MAX_PAGE_SIZE)
    except Exception:
        start = 0
        page_length = 500

    params = {"limit": page_length + 1, "offset": start}
    modified_clause = ""
    if modified_after:
        modified_clause = " AND `tabUOM Conversion Detail`.modified > %(modified_after)s"
        params["modified_after"] = modified_after

    rows = frappe.db.sql(
        """
        SELECT
            `tabUOM Conversion Detail`.parent  AS item_code,
            `tabUOM Conversion Detail`.uom,
            `tabUOM Conversion Detail`.conversion_factor,
            `tabUOM Conversion Detail`.modified
        FROM `tabUOM Conversion Detail`
        INNER JOIN `tabItem`
            ON  `tabItem`.name = `tabUOM Conversion Detail`.parent
        WHERE `tabUOM Conversion Detail`.parenttype = 'Item'
          AND `tabItem`.disabled = 0
          AND `tabItem`.is_sales_item = 1
          {modified_clause}
        ORDER BY `tabUOM Conversion Detail`.modified DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """.format(modified_clause=modified_clause),
        params,
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
def close_pos_session(opening_entry, closing_balances, notes=None):
    """Create and submit a POS Closing Entry for the current cashier's shift.

    The client supplies only its counted amount for each payment mode.  Invoice
    totals, expected amounts, and the opening balance are always derived from
    ERPNext documents on the server.
    """
    _require_login()
    frappe.has_permission("POS Closing Entry", "create", throw=True)

    try:
        opening = frappe.get_doc("POS Opening Entry", opening_entry)
    except frappe.DoesNotExistError:
        frappe.throw(_("Invalid POS Opening Entry: {0}").format(opening_entry))

    if opening.user != frappe.session.user:
        frappe.throw(
            _("POS Opening Entry {0} does not belong to the authenticated user.").format(
                opening.name
            ),
            frappe.PermissionError,
        )

    # Reuse the session/profile membership checks used by the other POS APIs.
    pos = _load_pos_profile(opening.pos_profile)
    if opening.company != pos.company:
        frappe.throw(
            _("POS Opening Entry {0} does not match its POS Profile company.").format(
                opening.name
            )
        )

    # Serialize close requests for this shift.  Reload after taking the lock
    # so a retry cannot create another closing document after a prior request
    # closed (or started closing) the same opening entry.
    frappe.db.sql(
        "SELECT name FROM `tabPOS Opening Entry` WHERE name = %s FOR UPDATE",
        (opening.name,),
    )
    opening.reload()

    if opening.docstatus != 1:
        frappe.throw(_("POS Opening Entry {0} must be submitted before it can be closed.").format(opening.name))

    if opening.status != "Open":
        if opening.pos_closing_entry:
            existing = frappe.get_doc("POS Closing Entry", opening.pos_closing_entry)
            return _build_closing_session_response(existing, opening.name)
        frappe.throw(_("POS Opening Entry {0} is not open.").format(opening.name))

    existing = frappe.db.get_value(
        "POS Closing Entry",
        {"pos_opening_entry": opening.name, "docstatus": ("in", [0, 1])},
        ["name", "docstatus"],
        as_dict=True,
    )
    if existing:
        if existing.docstatus == 1:
            return _build_closing_session_response(
                frappe.get_doc("POS Closing Entry", existing.name), opening.name
            )
        frappe.throw(
            _("A draft POS Closing Entry {0} already exists for this shift. Please resolve it before closing again.").format(
                existing.name
            )
        )

    closing_balances = frappe.parse_json(closing_balances)
    if not isinstance(closing_balances, list):
        frappe.throw(_("closing_balances must be a list"))

    counted_amounts = {}
    for row in closing_balances:
        if not isinstance(row, dict):
            frappe.throw(_("Each closing balance row must be an object"))

        mode_of_payment = (row.get("mode_of_payment") or "").strip()
        if not mode_of_payment:
            frappe.throw(_("Each closing balance row must have mode_of_payment"))
        if mode_of_payment in counted_amounts:
            frappe.throw(_("Duplicate closing balance for payment mode '{0}'").format(mode_of_payment))

        try:
            closing_amount = float(row.get("closing_amount"))
        except (TypeError, ValueError):
            frappe.throw(
                _("closing_amount must be a number for payment mode '{0}'").format(
                    mode_of_payment
                )
            )
        counted_amounts[mode_of_payment] = flt(closing_amount)

    from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
        make_closing_entry_from_opening,
    )

    # ERPNext derives the shift invoices, payment totals, and linked documents.
    closing = make_closing_entry_from_opening(opening.as_dict())
    closing.period_end_date = now_datetime()
    closing.posting_date = nowdate()
    closing.user = frappe.session.user
    closing.pos_profile = pos.name
    closing.company = opening.company

    # The helper starts payment rows from invoice payments.  Add each opening
    # balance exactly as ERPNext's POS Closing Entry form does before it adds
    # the transaction totals.
    reconciliation_by_mode = {
        row.mode_of_payment: row for row in closing.payment_reconciliation
    }
    for balance in opening.get("balance_details") or []:
        reconciliation = reconciliation_by_mode.get(balance.mode_of_payment)
        if reconciliation:
            reconciliation.opening_amount = flt(balance.opening_amount)
            reconciliation.expected_amount = flt(reconciliation.expected_amount) + flt(
                balance.opening_amount
            )
        else:
            reconciliation = closing.append(
                "payment_reconciliation",
                {
                    "mode_of_payment": balance.mode_of_payment,
                    "opening_amount": flt(balance.opening_amount),
                    "expected_amount": flt(balance.opening_amount),
                },
            )
            reconciliation_by_mode[balance.mode_of_payment] = reconciliation

    unknown_modes = set(counted_amounts) - set(reconciliation_by_mode)
    if unknown_modes:
        frappe.throw(
            _("Payment mode(s) are not part of this POS shift: {0}").format(
                ", ".join(sorted(unknown_modes))
            )
        )

    for mode_of_payment, closing_amount in counted_amounts.items():
        reconciliation = reconciliation_by_mode[mode_of_payment]
        reconciliation.closing_amount = closing_amount
        # Do not trust the client-provided difference; calculate it from
        # ERPNext's server-derived expected amount.
        reconciliation.difference = flt(closing_amount) - flt(reconciliation.expected_amount)

    if notes:
        # Standard POS Closing Entry has no remarks field.  Respect a custom
        # field when present; otherwise preserve the note on the document's
        # timeline after insertion.
        if closing.meta.has_field("remarks"):
            closing.remarks = notes

    savepoint = "close_pos_session_{0}".format(frappe.generate_hash(length=10))
    frappe.db.savepoint(savepoint)
    try:
        closing.insert()
        if notes and not closing.meta.has_field("remarks"):
            closing.add_comment("Comment", text=notes)
        closing.submit()
    except Exception:
        # Keep a failed close from leaving a draft POS-CLO document behind.
        # Some framework/database paths can clear savepoints before this
        # handler runs.  If that happens, fall back to a request-level rollback
        # and re-raise the original close error instead of masking it with
        # "SAVEPOINT ... does not exist".
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            rollback_traceback = frappe.get_traceback()
            try:
                frappe.db.rollback()
            except Exception:
                rollback_traceback = "{0}\n\nFull rollback also failed:\n{1}".format(
                    rollback_traceback,
                    frappe.get_traceback(),
                )
            frappe.log_error(
                rollback_traceback,
                _("POS close rollback failed for {0}").format(opening.name),
            )
        raise

    return _build_closing_session_response(closing, opening.name)


def _build_closing_session_response(closing, opening_entry):
    """Serialize a POS Closing Entry in the Electron close-session format."""
    return {
        "closing_entry": closing.name,
        "name": closing.name,
        "opening_entry": opening_entry,
        "status": "Closed",
        "grand_total": closing.grand_total,
        "net_total": closing.net_total,
        "total_quantity": closing.total_quantity,
        "payment_reconciliation": [row.as_dict() for row in closing.payment_reconciliation],
    }


def _get_open_pos_opening_entry(opening_entry):
    if not opening_entry:
        frappe.throw(_("opening_entry is required"))

    doc = frappe.get_doc("POS Opening Entry", opening_entry)
    doc.check_permission("read")

    if doc.docstatus != 1 or doc.status != "Open":
        frappe.throw(_("POS Opening Entry {0} is not open").format(opening_entry))

    if doc.user != frappe.session.user:
        frappe.throw(_("POS Opening Entry belongs to another user"), frappe.PermissionError)

    return doc


@frappe.whitelist()
def get_pos_closing_summary(opening_entry):
    _require_login()

    opening = _get_open_pos_opening_entry(opening_entry)

    from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import (
        make_closing_entry_from_opening,
    )

    closing_entry = make_closing_entry_from_opening(opening)
    reconciliation_by_mode = {
        row.mode_of_payment: row for row in closing_entry.get("payment_reconciliation") or []
    }
    for balance in opening.get("balance_details") or []:
        reconciliation = reconciliation_by_mode.get(balance.mode_of_payment)
        if reconciliation:
            reconciliation.opening_amount = flt(balance.opening_amount)
            reconciliation.expected_amount = flt(reconciliation.expected_amount) + flt(
                balance.opening_amount
            )
        else:
            reconciliation = closing_entry.append(
                "payment_reconciliation",
                {
                    "mode_of_payment": balance.mode_of_payment,
                    "opening_amount": flt(balance.opening_amount),
                    "expected_amount": flt(balance.opening_amount),
                },
            )
            reconciliation_by_mode[balance.mode_of_payment] = reconciliation

    invoice_count = len(closing_entry.get("pos_transactions") or [])
    if not invoice_count:
        invoice_count = len(closing_entry.get("pos_invoices") or []) + len(
            closing_entry.get("sales_invoices") or []
        )

    return {
        "openingEntry": closing_entry.pos_opening_entry,
        "posProfile": closing_entry.pos_profile,
        "user": closing_entry.user,
        "company": closing_entry.company,
        "periodStart": str(closing_entry.period_start_date),
        "payments": [
            {
                "mode_of_payment": p.mode_of_payment,
                "opening_amount": flt(p.opening_amount, 2),
                "expected_amount": flt(p.expected_amount, 2),
                "collected_amount": flt(p.expected_amount - p.opening_amount, 2),
            }
            for p in closing_entry.get("payment_reconciliation") or []
        ],
        "invoiceCount": invoice_count,
        "totalOpening": flt(
            sum(
                flt(p.opening_amount)
                for p in closing_entry.get("payment_reconciliation") or []
            ),
            2,
        ),
        "totalExpected": flt(
            sum(
                flt(p.expected_amount)
                for p in closing_entry.get("payment_reconciliation") or []
            ),
            2,
        ),
        "isEstimate": False,
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


# ---------------------------------------------------------------------------
# POS Refund / Return (online only)
# ---------------------------------------------------------------------------

_FBR_INVALID_NUMBERS = {"", "none", "null", "n/a", "na", "not available"}


def _pos_profile_user_allowed(pos, fieldname, user):
    if not pos.meta.has_field(fieldname):
        return None

    allowed_users = {
        getattr(row, "user", None)
        for row in (pos.get(fieldname) or [])
        if getattr(row, "user", None)
    }

    if not allowed_users:
        return False

    return user in allowed_users


def _require_refund_permission(pos):
    """Require a refund role, or an explicit POS Profile user allow-list entry."""
    user = frappe.session.user
    roles = set(frappe.get_roles(user))

    if roles.intersection(_ALLOWED_REFUND_ROLES):
        return

    allowed = _pos_profile_user_allowed(pos, "custom_refund_allowed_users", user)
    if allowed is True:
        return

    if allowed is False:
        frappe.throw(
            _("Refund is not allowed for user {0} on POS Profile {1}").format(
                user, pos.name
            ),
            frappe.PermissionError,
        )

    frappe.throw(
        _("Refund requires POS Supervisor or System Manager role"),
        frappe.PermissionError,
    )


def _returned_qty_by_row(original_name):
    """Absolute returned quantity per original POS Invoice Item row.

    Sums quantities from all submitted return POS Invoices linked to the
    original via the standard ``pos_invoice_item`` reference set by make_return_doc.
    """
    result = {}
    returns = frappe.get_all(
        "POS Invoice",
        filters={"return_against": original_name, "is_return": 1, "docstatus": 1},
        pluck="name",
    )
    if not returns:
        return result
    rows = frappe.get_all(
        "POS Invoice Item",
        filters={"parent": ("in", returns), "parenttype": "POS Invoice"},
        fields=["pos_invoice_item", "qty"],
    )
    for r in rows:
        key = r.get("pos_invoice_item")
        if not key:
            continue
        result[key] = flt(result.get(key, 0)) + abs(flt(r.get("qty")))
    return result


def _validate_refund_quantities(original, requested):
    """Reject zero/negative or over-remaining requested quantities (re-checkable inside a txn)."""
    returned = _returned_qty_by_row(original.name)
    row_by_name = {r.name: r for r in original.items}
    for row_name, qty in requested.items():
        row = row_by_name.get(row_name)
        if not row:
            frappe.throw(
                _("Row {0} does not belong to invoice {1}").format(row_name, original.name)
            )
        if qty <= 0:
            frappe.throw(_("Return quantity must be positive for {0}").format(row.item_code))
        sold = abs(flt(row.qty))
        already = flt(returned.get(row_name, 0))
        remaining = flt(sold - already, 3)
        # Tolerance appropriate to ERPNext qty precision (3 dp)
        if qty > flt(remaining + 0.001, 3):
            frappe.throw(
                _("Return quantity {0} exceeds remaining {1} for {2}").format(
                    qty, remaining, row.item_code
                )
            )


def _preserve_original_return_row_values(row, original_row, qty):
    """Keep refund unit valuation tied to the original submitted row."""
    sold = abs(flt(original_row.qty))
    if sold <= 0:
        frappe.throw(_("Original quantity is invalid for row {0}").format(original_row.name))

    ratio = abs(flt(qty, 3)) / sold
    row.item_code = original_row.item_code
    row.item_name = original_row.item_name
    row.uom = original_row.uom
    row.stock_uom = original_row.stock_uom
    row.conversion_factor = flt(original_row.conversion_factor) or 1
    row.warehouse = original_row.warehouse
    row.rate = flt(original_row.rate, 6)
    row.price_list_rate = flt(original_row.price_list_rate, 6)
    row.discount_percentage = flt(original_row.discount_percentage, 6)
    row.discount_amount = flt(original_row.discount_amount, 6)
    row.net_rate = flt(original_row.net_rate, 6)
    row.amount = -abs(flt(original_row.amount, 6) * ratio)
    row.net_amount = -abs(flt(original_row.net_amount, 6) * ratio)


def _validate_return_doc_against_original(return_doc, original):
    original_by_name = {r.name: r for r in original.items}

    for row in return_doc.items:
        original_row_name = getattr(row, "pos_invoice_item", None)
        original_row = original_by_name.get(original_row_name)

        if not original_row:
            frappe.throw(
                _("Return row {0} is not linked to the original invoice").format(row.item_code)
            )

        checks = [
            ("rate", flt(row.rate, 6), flt(original_row.rate, 6)),
            ("price_list_rate", flt(row.price_list_rate, 6), flt(original_row.price_list_rate, 6)),
            (
                "discount_percentage",
                flt(row.discount_percentage, 6),
                flt(original_row.discount_percentage, 6),
            ),
            ("uom", row.uom, original_row.uom),
            (
                "conversion_factor",
                flt(row.conversion_factor, 6),
                flt(original_row.conversion_factor, 6) or 1,
            ),
        ]

        for label, actual, expected in checks:
            if actual != expected:
                frappe.throw(
                    _("Return {0} mismatch for item {1}: {2} != {3}").format(
                        label, row.item_code, actual, expected
                    )
                )


def _find_existing_return(terminal_refund_id):
    """Return the name of an existing return POS Invoice for this refund UUID, or None."""
    if not frappe.get_meta("POS Invoice").has_field("custom_terminal_refund_id"):
        return None
    return frappe.db.get_value(
        "POS Invoice",
        {"custom_terminal_refund_id": terminal_refund_id},
        "name",
    )


def _normalize_refund_fbr_status(status, invoice_number):
    """Accepted only with an explicit Accepted status AND a real FBR invoice number."""
    has_number = (
        bool(invoice_number)
        and str(invoice_number).strip().lower() not in _FBR_INVALID_NUMBERS
    )
    if status == "Accepted" and has_number:
        return "Accepted"
    if status == "Failed":
        return "Failed"
    return "Pending"


def _set_refund_payments(doc, pos, payments_data):
    """Set negative refund payment rows that balance the (negative) return grand total.

    - Modes must be allowed by the active POS Profile.
    - Amounts are normalised to ERPNext's negative return convention.
    - Total refunded cannot exceed the calculated refund payable.
    - Split payments are preserved.
    """
    grand_total = flt(doc.grand_total, 2)  # negative for a return
    allowed_modes = {p.mode_of_payment for p in (pos.get("payments") or [])}
    if not allowed_modes:
        frappe.throw(_("POS Profile has no payment modes configured"))

    rows = []
    if isinstance(payments_data, list) and len(payments_data) == 1:
        # Single chosen mode: snap to the exact server-computed refund total.
        mode = (payments_data[0].get("mode_of_payment") or "").strip()
        if not mode:
            frappe.throw(_("Each refund payment row must have mode_of_payment"))
        if mode not in allowed_modes:
            frappe.throw(
                _("Payment mode '{0}' is not allowed in POS Profile {1}").format(mode, pos.name)
            )
        rows.append((mode, grand_total))
    elif isinstance(payments_data, list) and payments_data:
        for p in payments_data:
            mode = (p.get("mode_of_payment") or "").strip()
            amount = flt(p.get("amount") or 0, 2)
            if not mode:
                frappe.throw(_("Each refund payment row must have mode_of_payment"))
            if mode not in allowed_modes:
                frappe.throw(
                    _("Payment mode '{0}' is not allowed in POS Profile {1}").format(mode, pos.name)
                )
            if amount == 0:
                continue
            rows.append((mode, -abs(amount)))  # refund payments are negative
    else:
        # Default to the original invoice's primary mode, else the first allowed mode.
        default_mode = None
        original = frappe.get_doc("POS Invoice", doc.return_against)
        for p in (original.get("payments") or []):
            default_mode = p.mode_of_payment
            break
        if not default_mode:
            default_mode = sorted(allowed_modes)[0]
        if default_mode not in allowed_modes:
            frappe.throw(
                _("Refund payment mode '{0}' is not allowed in POS Profile {1}").format(
                    default_mode, pos.name
                )
            )
        rows.append((default_mode, grand_total))

    total = flt(sum(a for _, a in rows), 2)
    if abs(total) > abs(grand_total) + 0.01:
        frappe.throw(
            _("Refund payments {0} exceed the refund payable {1}").format(total, grand_total)
        )
    if abs(total - grand_total) > 0.01:
        frappe.throw(
            _("Refund payments {0} must balance the refund total {1}").format(total, grand_total)
        )

    doc.set("payments", [])
    for mode, amount in rows:
        doc.append("payments", {"mode_of_payment": mode, "amount": amount})
    doc.paid_amount = total
    doc.base_paid_amount = total


def _build_refund_response(doc, duplicate=False):
    meta = doc.meta

    def _cf(fieldname):
        return getattr(doc, fieldname, None) if meta.has_field(fieldname) else None

    items_out = [
        {
            "item_code": r.item_code,
            "qty": flt(r.qty, 3),
            "rate": flt(r.rate, 2),
            "net_rate": flt(getattr(r, "net_rate", 0), 2),
            "amount": flt(r.amount, 2),
            "net_amount": flt(getattr(r, "net_amount", 0), 2),
            "sales_tax": flt(getattr(r, "custom_fbr_sales_tax", 0) or 0, 2),
            "value_excluding_tax": flt(
                getattr(r, "custom_fbr_value_excluding_tax", 0) or 0, 2
            ),
            "original_row_name": getattr(r, "pos_invoice_item", None),
        }
        for r in (doc.get("items") or [])
    ]
    payments_out = [
        {"mode_of_payment": p.mode_of_payment, "amount": flt(p.amount, 2)}
        for p in (doc.get("payments") or [])
    ]
    fbr_status = _cf("custom_fbr_status")
    fbr_invoice_number = _cf("custom_fbr_invoice_number")
    merchandise_refund = abs(
        flt(sum(flt(r.amount) for r in (doc.get("items") or [])), 2)
    )
    gst_refund = abs(
        flt(
            sum(
                flt(getattr(r, "custom_fbr_sales_tax", 0) or 0)
                for r in (doc.get("items") or [])
            ),
            2,
        )
    )
    total_refund = abs(flt(doc.grand_total, 2))

    non_refundable_fee = 0.0
    if getattr(doc, "return_against", None):
        original = frappe.get_doc("POS Invoice", doc.return_against)
        for tax in original.get("taxes") or []:
            if (getattr(tax, "description", "") or "").strip() == "FBR POS Service Fee":
                non_refundable_fee = abs(flt(getattr(tax, "tax_amount", 0) or 0, 2))
                break

    return {
        "success": True,
        "duplicate": duplicate,
        "invoice": {
            "name": doc.name,
            "return_against": getattr(doc, "return_against", None),
            "is_return": True,
            "posting_date": str(doc.posting_date),
            "posting_time": str(doc.posting_time),
            "customer": doc.customer,
            "grand_total": flt(doc.grand_total, 2),
            "rounded_total": flt(getattr(doc, "rounded_total", None) or doc.grand_total, 2),
        },
        "items": items_out,
        "payments": payments_out,
        "fbr": {
            "status": _normalize_refund_fbr_status(fbr_status, fbr_invoice_number),
            "response_code": str(_cf("custom_fbr_http_status") or ""),
            "invoice_number": fbr_invoice_number or "",
            "message": _cf("custom_fbr_error") or "",
        },
        "refund_totals": {
            "merchandise_refund": merchandise_refund,
            "gst_refund": gst_refund,
            "non_refundable_fbr_pos_fee": non_refundable_fee,
            "total_refund": total_refund,
        },
    }


@frappe.whitelist()
def get_pos_invoice_for_refund(invoice_name):
    """Return authoritative original-invoice data and refundable quantities.

    Never uses current Item Price or current Item tax config — every value comes
    from the original submitted POS Invoice and its FBR item snapshot.
    """
    _require_login()
    if not invoice_name:
        frappe.throw(_("invoice_name is required"))

    original = frappe.get_doc("POS Invoice", invoice_name)
    original.check_permission("read")

    if original.docstatus != 1:
        frappe.throw(_("Original POS Invoice must be submitted"))
    if cint(getattr(original, "is_return", 0)):
        frappe.throw(_("Cannot refund a return invoice"))

    returned = _returned_qty_by_row(original.name)

    items = []
    any_remaining = False
    for row in original.items:
        sold = abs(flt(row.qty))
        already = flt(returned.get(row.name, 0))
        remaining = flt(sold - already, 3)
        if remaining > 0.0009:
            any_remaining = True
        items.append({
            "row_name": row.name,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "uom": row.uom,
            "stock_uom": row.stock_uom,
            "conversion_factor": flt(row.conversion_factor) or 1,
            "warehouse": row.warehouse,
            "sold_qty": sold,
            "already_returned_qty": already,
            "remaining_qty": max(remaining, 0.0),
            "rate": flt(row.rate, 2),
            "price_list_rate": flt(row.price_list_rate, 2),
            "discount_percentage": flt(row.discount_percentage, 2),
            "discount_amount": flt(row.discount_amount, 2),
            "amount": flt(row.amount, 2),
            "net_rate": flt(row.net_rate, 2),
            "net_amount": flt(row.net_amount, 2),
            # Original FBR snapshot (never recomputed from current config here)
            "fbr_tax_category": getattr(row, "custom_fbr_tax_category", None),
            "fbr_sale_type": getattr(row, "custom_fbr_sale_type", None),
            "fbr_tax_rate": flt(getattr(row, "custom_fbr_tax_rate", 0) or 0, 2),
            "is_third_schedule": cint(getattr(row, "custom_fbr_is_third_schedule", 0) or 0),
            "mrp": flt(getattr(row, "custom_fbr_mrp", 0) or 0, 2),
            "value_excluding_tax": flt(getattr(row, "custom_fbr_value_excluding_tax", 0) or 0, 2),
            "sales_tax": flt(getattr(row, "custom_fbr_sales_tax", 0) or 0, 2),
            "retail_price": flt(getattr(row, "custom_fbr_retail_price", 0) or 0, 2),
            "hs_code": getattr(row, "custom_fbr_hs_code", None),
        })

    payments = [
        {"mode_of_payment": p.mode_of_payment, "amount": flt(p.amount, 2)}
        for p in (original.get("payments") or [])
    ]
    fbr_pos_service_fee = 0.0
    for tax in original.get("taxes") or []:
        if (getattr(tax, "description", "") or "").strip() == "FBR POS Service Fee":
            fbr_pos_service_fee = abs(flt(getattr(tax, "tax_amount", 0) or 0, 2))
            break

    return {
        "original_invoice": original.name,
        "customer": original.customer,
        "customer_name": getattr(original, "customer_name", None),
        "company": original.company,
        "pos_profile": original.pos_profile,
        "posting_date": str(original.posting_date),
        "posting_time": str(original.posting_time),
        "grand_total": flt(original.grand_total, 2),
        "rounded_total": flt(getattr(original, "rounded_total", None) or original.grand_total, 2),
        "fbr_pos_service_fee": fbr_pos_service_fee,
        "payments": payments,
        "fbr_status": getattr(original, "custom_fbr_status", None),
        "fbr_invoice_number": getattr(original, "custom_fbr_invoice_number", None),
        "any_remaining": any_remaining,
        "items": items,
    }


@frappe.whitelist()
def submit_pos_refund(
    terminal_refund_id,
    original_invoice,
    pos_opening_entry=None,
    reason=None,
    items=None,
    payments=None,
):
    """Create and submit a return POS Invoice against an original sale.

    Idempotent on custom_terminal_refund_id. Reuses the standard ERPNext return
    mapper for original rates/accounts, restricts it to the requested quantities,
    and lets the existing FBR hooks build the InvoiceType=2 credit note and submit
    to FBR exactly once. Electron never calls FBR. The Rs.1 POS service fee is
    neither added nor refunded on returns (see fbr_pos.accounting).
    """
    _require_login()

    if not frappe.has_permission("POS Invoice", "create"):
        frappe.throw(_("Not permitted to create POS Invoice"), frappe.PermissionError)
    if not frappe.has_permission("POS Invoice", "submit"):
        frappe.throw(_("Not permitted to submit POS Invoice"), frappe.PermissionError)
    if not terminal_refund_id:
        frappe.throw(_("terminal_refund_id is required"))

    # Idempotency: never create a second return for the same refund UUID.
    existing_name = _find_existing_return(terminal_refund_id)
    if existing_name:
        return _build_refund_response(frappe.get_doc("POS Invoice", existing_name), duplicate=True)

    if not original_invoice:
        frappe.throw(_("original_invoice is required"))

    items = _parse_json_param(items, "items")
    payments = _parse_json_param(payments, "payments") if payments else []
    if not isinstance(items, list) or not items:
        frappe.throw(_("At least one return item is required"))

    original = frappe.get_doc("POS Invoice", original_invoice)
    original.check_permission("read")
    if original.docstatus != 1:
        frappe.throw(_("Original POS Invoice must be submitted"))
    if cint(getattr(original, "is_return", 0)):
        frappe.throw(_("Cannot refund a return invoice"))

    # POS Profile membership + an active submitted opening entry for this cashier.
    pos = _load_pos_profile(original.pos_profile)
    _require_refund_permission(pos)
    _validate_pos_opening_entry(pos.name)

    # Aggregate requested quantities by original child-row name.
    requested = {}
    for it in items:
        row_name = (it.get("original_row_name") or "").strip()
        qty = abs(flt(it.get("qty") or it.get("qty_to_return") or 0, 3))
        if not row_name:
            frappe.throw(_("Each return item must include original_row_name"))
        if qty <= 0:
            continue
        requested[row_name] = flt(requested.get(row_name, 0) + qty, 3)
    if not requested:
        frappe.throw(_("No positive return quantity requested"))

    _validate_refund_quantities(original, requested)

    # Standard ERPNext return mapper (copies original rates/accounts; accounts for prior returns).
    from erpnext.controllers.sales_and_purchase_return import make_return_doc

    return_doc = make_return_doc("POS Invoice", original.name)
    return_doc.pos_profile = original.pos_profile
    return_doc.set_posting_time = 1
    return_doc.posting_date = nowdate()
    return_doc.posting_time = nowtime()

    # Restrict to requested rows and quantities (return quantities are negative).
    original_by_name = {r.name: r for r in original.items}
    kept = []
    for row in return_doc.items:
        original_row = getattr(row, "pos_invoice_item", None)
        if original_row in requested:
            row.qty = -abs(flt(requested[original_row], 3))
            _preserve_original_return_row_values(
                row,
                original_by_name[original_row],
                requested[original_row],
            )
            kept.append(row)
    if not kept:
        frappe.throw(_("No matching original rows for the requested return"))
    return_doc.set("items", kept)
    _validate_return_doc_against_original(return_doc, original)

    if return_doc.meta.has_field("custom_terminal_refund_id"):
        return_doc.custom_terminal_refund_id = terminal_refund_id
    if return_doc.meta.has_field("custom_terminal_id"):
        return_doc.custom_terminal_id = getattr(original, "custom_terminal_id", None) or ""
    if reason and return_doc.meta.has_field("remarks"):
        return_doc.remarks = reason

    # Apply FBR snapshot + accounting (negative sales tax, no service fee) so the
    # negative grand_total is known before computing refund payments. The validate
    # hook re-applies these idempotently on insert.
    from aimatic.fbr_pos.accounting import apply_fbr_accounting_rows
    from aimatic.fbr_pos.payload_builder import build_pos_payload

    return_doc.run_method("calculate_taxes_and_totals")
    build_pos_payload(return_doc)
    apply_fbr_accounting_rows(return_doc)
    _validate_return_doc_against_original(return_doc, original)

    _set_refund_payments(return_doc, pos, payments)

    sp = "submit_pos_refund"
    frappe.db.savepoint(sp)
    try:
        # Re-validate remaining quantities inside the transaction to defeat races.
        _validate_refund_quantities(original, requested)
        return_doc.insert()   # validate hook: clears copied FBR fields, builds InvoiceType=2 payload, accounting rows
        return_doc.submit()   # before_submit hook: single FBR refund submission
        frappe.db.release_savepoint(sp)
    except frappe.UniqueValidationError:
        frappe.db.rollback(save_point=sp)
        existing_name = _find_existing_return(terminal_refund_id)
        if existing_name:
            return _build_refund_response(frappe.get_doc("POS Invoice", existing_name), duplicate=True)
        raise
    except Exception:
        frappe.db.rollback(save_point=sp)
        raise

    return _build_refund_response(return_doc, duplicate=False)
