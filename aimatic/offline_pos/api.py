import json
import secrets

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime, nowdate, nowtime
from frappe.utils.data import sha256_hash
from frappe.utils.password import check_password

from aimatic.pos_shared import returned_qty_by_row
from aimatic.aimatic.offline_pos.device_auth import hash_device_token, validate_device_proof

_MAX_PAGE_SIZE = 1000
_ALLOWED_POS_ADMIN_ACTIONS = {"setup_pin", "reset_pin", "change_credentials", "close_shift", "void_item"}
_ALLOWED_POS_ADMIN_ROLES = {"POS Supervisor", "System Manager"}
_ALLOWED_REFUND_ROLES = {"POS Supervisor", "System Manager"}
_ALLOWED_CLOSE_SHIFT_ROLES = {"POS Supervisor", "System Manager"}
_ALLOWED_CASHIER_ROLES = {"POS User", "POS Supervisor", "System Manager"}
_CASHIER_OFFLINE_LOGIN_VALID_DAYS = 7

# Master-data doctypes the terminal client is allowed to read via
# get_terminal_resource/list_terminal_resources below - see those functions'
# docstrings. Explicit and reviewable in one place, same reasoning as the
# existing get_item_barcodes/get_uom_conversions endpoints just below.
_TERMINAL_MASTER_DATA_DOCTYPES = {
    "POS Profile",
    "Company",
    "Sales Taxes and Charges Template",
    "Mode of Payment",
    "Coupon Code",
    "Customer",
    "Customer Group",
    "Territory",
    "Item",
    "Item Price",
    "Bin",
    "Branch",
    "Print Format",
}


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


def _validate_pos_opening_entry(pos_profile_name, user=None):
    """Raise if there is no active submitted POS Opening Entry for the given
    user (defaults to the authenticated session user) and profile."""
    user = user or frappe.session.user
    opening_entries = frappe.get_all(
        "POS Opening Entry",
        fields=["name", "period_start_date"],
        filters={
            "pos_profile": pos_profile_name,
            "user": user,
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


def _get_pos_profile_or_throw(pos_profile_name):
    """Load a POS Profile by existence/disabled state only.

    Unlike _load_pos_profile, this does not check whether the authenticated
    caller belongs to the profile's applicable_for_users list. Cashier-aware
    endpoints are called by a terminal's own fixed API identity on behalf of
    a separate human cashier, so "does the caller belong to this profile" is
    the wrong question — cashier membership is checked independently against
    the actual cashier_user via _validate_cashier_identity.
    """
    if not frappe.db.exists("POS Profile", pos_profile_name):
        frappe.throw(_("Invalid POS Profile: {0}").format(pos_profile_name))

    pos = frappe.get_cached_doc("POS Profile", pos_profile_name)
    if cint(pos.disabled):
        frappe.throw(_("POS Profile {0} is disabled").format(pos.name))

    return pos


def _validate_cashier_identity(cashier_user, pos, required_roles=None):
    """Validate a human cashier against current server state.

    Checks the cashier exists, is enabled, holds one of required_roles
    (default _ALLOWED_CASHIER_ROLES), and is permitted on this POS Profile's
    applicable_for_users list.  Raises frappe.PermissionError with a clear
    message on any failure.  Returns the cashier's role set (minus All/Guest).
    """
    if not cashier_user:
        frappe.throw(_("cashier_user is required"), frappe.PermissionError)

    if not frappe.db.exists("User", cashier_user):
        frappe.throw(_("Invalid cashier: {0}").format(cashier_user), frappe.PermissionError)

    if not cint(frappe.db.get_value("User", cashier_user, "enabled")):
        frappe.throw(_("Cashier {0} is disabled").format(cashier_user), frappe.PermissionError)

    roles = set(frappe.get_roles(cashier_user)) - {"All", "Guest"}
    required = required_roles or _ALLOWED_CASHIER_ROLES
    if not roles.intersection(required):
        frappe.throw(
            _("Cashier {0} is not authorized for this POS operation").format(cashier_user),
            frappe.PermissionError,
        )

    user_list = [r.user for r in (pos.get("applicable_for_users") or [])]
    if user_list and cashier_user not in user_list:
        frappe.throw(
            _("Cashier {0} is not permitted on POS Profile {1}").format(cashier_user, pos.name),
            frappe.PermissionError,
        )

    return roles


def _require_open_entry_for_cashier(opening_entry, cashier_user, pos):
    """Load a submitted, Open POS Opening Entry and confirm it belongs to
    cashier_user and this POS Profile.  Never matches on the authenticated
    (terminal) session user — a terminal is shared across cashiers.
    """
    if not opening_entry:
        frappe.throw(_("opening_entry is required"))
    if not cashier_user:
        frappe.throw(_("cashier_user is required"), frappe.PermissionError)

    entry = frappe.db.get_value(
        "POS Opening Entry",
        opening_entry,
        ["name", "user", "pos_profile", "company", "docstatus", "status"],
        as_dict=True,
    )
    if not entry:
        frappe.throw(_("Invalid POS Opening Entry: {0}").format(opening_entry))

    if entry.user != cashier_user:
        frappe.throw(
            _("POS Opening Entry {0} does not belong to cashier {1}").format(
                opening_entry, cashier_user
            ),
            frappe.PermissionError,
        )

    if entry.pos_profile != pos.name:
        frappe.throw(
            _("POS Opening Entry {0} does not match POS Profile {1}").format(opening_entry, pos.name)
        )

    if entry.docstatus != 1 or entry.status != "Open":
        frappe.throw(_("POS Opening Entry {0} is not open").format(opening_entry))

    return entry


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


def _is_https_request():
    request = getattr(frappe.local, "request", None)
    if not request:
        return False

    if getattr(request, "scheme", "") == "https":
        return True

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    if forwarded_proto.split(",")[0].strip().lower() == "https":
        return True

    forwarded_ssl = request.headers.get("X-Forwarded-Ssl", "")
    if forwarded_ssl.lower() == "on":
        return True

    return False


def _require_https_for_pos_admin_authorization():
    if not _is_https_request():
        frappe.throw(_("HTTPS is required for supervisor authorization"))


@frappe.whitelist(allow_guest=True)
@rate_limit(key="username", limit=5, seconds=30)
def provision_terminal_credentials(username, password):
    """First-run Electron setup: exchange an ERPNext username+password for that
    user's terminal api_key/api_secret, so the desktop app never requires
    copy-pasting keys out of Frappe's User settings. The password is verified
    once via Frappe's own check_password and never stored; only the resulting
    key/secret pair — the same terminal-token credential Electron has always
    used — is returned and persisted locally by the client from then on.

    allow_guest is deliberate and reviewed: a fresh Electron install has no
    api_key/api_secret yet, so nothing else can authenticate this call. The
    response is narrowly the calling user's own credential, nothing else.
    Rate-limited (5 attempts / 5 min per username+IP) because check_password
    alone does not get Frappe's normal login-attempt lockout — that lives in
    the full LoginManager flow, not in check_password.

    api_secret is a Frappe "Password" field (encrypted at rest, never
    readable back in plaintext) — exactly like the stock User settings
    "Generate Keys" button, calling this a second time regenerates and
    invalidates the previous secret. If multiple terminals should stay
    independently valid, provision each from its own dedicated ERPNext user
    rather than reusing one username across terminals.
    """
    if not _is_https_request():
        frappe.throw(_("HTTPS is required for terminal credential provisioning"))

    if not username or not password:
        frappe.throw(_("Username and password are required"))

    if not frappe.db.exists("User", username):
        _audit_pos_admin_action(None, "provision_terminal_credentials", "electron-provisioning", "invalid_user")
        frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)

    if not cint(frappe.db.get_value("User", username, "enabled")):
        _audit_pos_admin_action(username, "provision_terminal_credentials", "electron-provisioning", "disabled_user")
        frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)

    try:
        check_password(username, password)
    except Exception:
        _audit_pos_admin_action(username, "provision_terminal_credentials", "electron-provisioning", "failed")
        frappe.throw(_("Invalid credentials"), frappe.AuthenticationError)

    roles = set(frappe.get_roles(username)) - {"All", "Guest"}
    if not roles.intersection(_ALLOWED_CASHIER_ROLES):
        _audit_pos_admin_action(username, "provision_terminal_credentials", "electron-provisioning", "missing_role")
        frappe.throw(_("User is not authorized for POS terminal setup"), frappe.PermissionError)

    user_doc = frappe.get_doc("User", username)
    if not user_doc.api_key:
        user_doc.api_key = frappe.generate_hash(length=15)
    api_secret = frappe.generate_hash(length=15)
    user_doc.api_secret = api_secret
    user_doc.save(ignore_permissions=True)
    frappe.db.commit()

    _audit_pos_admin_action(username, "provision_terminal_credentials", "electron-provisioning", "success")

    return {"api_key": user_doc.api_key, "api_secret": api_secret}


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


def _consume_pos_admin_authorization_token(token, action, terminal_id):
    """Validate, single-use-consume, and audit a POS Admin Authorization
    token. Shared by the whitelisted consume_pos_admin_authorization (called
    directly by Electron for actions with no server document of their own,
    e.g. void_item) and by close_pos_session (called in-process, in the same
    DB transaction as the close itself, so a token is never burned if the
    close subsequently fails/rolls back for an unrelated reason).

    Returns the consumed POS Admin Authorization doc. Raises
    frappe.PermissionError/ValidationError on any failure, auditing each one.
    """
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

    return auth


@frappe.whitelist()
def consume_pos_admin_authorization(token, action, terminal_id):
    _require_login()
    _require_https_for_pos_admin_authorization()

    _validate_pos_admin_action_args(action, terminal_id)

    _consume_pos_admin_authorization_token(token, action, terminal_id)

    return {"success": True}


# ---------------------------------------------------------------------------
# POS device enrollment — binds a physical hardware_id to a POS Profile via a
# one-time, expiring, supervisor-issued code (QR). Redeeming is allow_guest
# because the device has no ERPNext session yet; the response is intentionally
# narrow (pos_profile/branch labels only, never prices/stock/customers/etc.)
# and the token is single-use and short-lived, same as POS Admin Authorization.
# ---------------------------------------------------------------------------

_DEVICE_ENROLLMENT_VALID_MINUTES = 10
_POS_ANDROID_OAUTH_APP_NAME = "Aimatic POS Android"


def _hash_device_token(token):
    return hash_device_token(token)


def _device_enrollment_qr_data_uri(enrollment_value):
    """Render the one-time enrollment value locally; never send it to a
    third-party QR service."""
    from frappe.twofactor import get_qr_svg_code

    encoded_svg = get_qr_svg_code(enrollment_value)
    if isinstance(encoded_svg, bytes):
        encoded_svg = encoded_svg.decode()
    return f"data:image/svg+xml;base64,{encoded_svg}"


def _get_pos_android_oauth_client_id():
    """Return the public OAuth client identifier needed by the APK.

    This value is intentionally not a client secret. Android receives it only
    after a valid one-time device enrollment redemption, keeping discovery and
    configuration in one narrow response.
    """
    client_id = frappe.db.get_value(
        "OAuth Client", {"app_name": _POS_ANDROID_OAUTH_APP_NAME}, "name"
    )
    if not client_id:
        frappe.throw(_("Ai Matic POS Android OAuth client is not configured"))
    return client_id


def _audit_device_action(user, hardware_id, pos_profile, status):
    if user and not frappe.db.exists("User", user):
        user = None

    frappe.get_doc({
        "doctype": "POS Device Audit Log",
        "user": user,
        "hardware_id": hardware_id,
        "pos_profile": pos_profile,
        "status": status,
        "created_at": now_datetime(),
    }).insert(ignore_permissions=True)


def require_active_device(hardware_id):
    """Raise unless hardware_id is bound to an enabled POS Device. Call this
    from any endpoint reachable with a user-session (Bearer) token so a
    revoked/disabled device is rejected even with a valid cashier token."""
    if not hardware_id:
        frappe.throw(_("hardware_id is required"), frappe.PermissionError)

    device = frappe.db.get_value(
        "POS Device", hardware_id, ["enabled", "pos_profile"], as_dict=True
    )
    if not device:
        _audit_device_action(frappe.session.user, hardware_id, None, "unknown_device")
        frappe.throw(_("This device is not enrolled"), frappe.PermissionError)
    if not cint(device.enabled):
        _audit_device_action(frappe.session.user, hardware_id, device.pos_profile, "device_disabled")
        frappe.throw(_("This device has been disabled"), frappe.PermissionError)

    return device.pos_profile


def _request_device_profile():
    return getattr(frappe.local, "aimatic_pos_profile", None)


def _is_bearer_authenticated_request():
    """True for Android's per-cashier OAuth session, False for Electron's
    terminal-token session. Electron sends `Authorization: token key:secret`
    (built in src/api/client.ts); an OAuth2 Bearer request always sends
    `Authorization: Bearer <access_token>` — the two are unambiguous from a
    single request header, so this needs no frappe core change."""
    request = getattr(frappe.local, "request", None)
    if not request:
        # Background jobs, direct Python calls, and the existing test suite do
        # not have an HTTP request. They are never Android OAuth requests and
        # must retain the legacy terminal-token/cashier_user behavior.
        return False
    return (request.headers.get("Authorization") or "").startswith("Bearer ")


def _enforce_bearer_profile(pos_profile):
    """Keep Android OAuth requests inside the enrolled device's POS Profile.

    The auth hook has already authenticated the device proof and recorded its
    bound profile. Electron's terminal-token requests deliberately bypass this
    check and retain their existing behaviour.
    """
    if not _is_bearer_authenticated_request():
        return

    bound_profile = _request_device_profile()
    if not bound_profile:
        frappe.throw(_("An enrolled Android POS device is required"), frappe.AuthenticationError)
    if bound_profile != pos_profile:
        frappe.throw(
            _("This device is not enrolled for POS Profile {0}").format(pos_profile),
            frappe.PermissionError,
        )


def _resolve_cashier_user(cashier_user, pos_profile, hardware_id, is_bearer):
    """Electron (terminal-token): unchanged — cashier_user stays whatever the
    client sends, exactly as submit_online_sale/submit_pos_refund have always
    done, because the terminal's own authenticated identity is a fixed
    service account, never the human cashier.

    Android (Bearer/OAuth): frappe.session.user IS the real, individually
    authenticated cashier by the time this runs (Frappe core resolves it
    before any whitelisted function executes) — so the client-supplied
    cashier_user is never trusted here, even if present; it's derived
    entirely from the session. Also enforces the request's hardware_id is an
    enrolled, enabled device bound to this exact pos_profile.
    """
    if not is_bearer:
        return cashier_user

    if frappe.session.user in (None, "", "Guest"):
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)
    request_hardware_id = getattr(frappe.local, "aimatic_pos_hardware_id", None)
    if hardware_id and request_hardware_id and hardware_id != request_hardware_id:
        frappe.throw(_("Request hardware ID does not match the enrolled device"), frappe.PermissionError)
    bound_profile = _request_device_profile()
    if not bound_profile:
        bound_profile = (
            validate_device_proof(hardware_id=hardware_id)
            if request_hardware_id
            else require_active_device(hardware_id)
        )
    if bound_profile != pos_profile:
        frappe.throw(
            _("This device is not enrolled for POS Profile {0}").format(pos_profile),
            frappe.PermissionError,
        )

    return frappe.session.user


@frappe.whitelist()
def generate_device_enrollment_code(pos_profile):
    """Desk-callable: a POS Supervisor/System Manager generates a one-time
    enrollment code for a physical Android device, scoped to one POS Profile."""
    _require_login()

    if not pos_profile:
        frappe.throw(_("POS Profile is required"))

    # Deliberately not _load_pos_profile(): that also enforces the calling
    # user is in applicable_for_users, which is a cashier-membership list and
    # unrelated to who may administratively enroll a device. Role check below
    # is the actual gate for this action.
    try:
        pos = frappe.get_cached_doc("POS Profile", pos_profile)
    except frappe.DoesNotExistError:
        frappe.throw(_("Invalid POS Profile: {0}").format(pos_profile))

    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(_ALLOWED_POS_ADMIN_ROLES):
        frappe.throw(_("Not permitted to enroll POS devices"), frappe.PermissionError)

    token = secrets.token_urlsafe(32)
    expires_at = add_to_date(now_datetime(), minutes=_DEVICE_ENROLLMENT_VALID_MINUTES)

    frappe.get_doc({
        "doctype": "POS Device Enrollment",
        "pos_profile": pos.name,
        "token_hash": _hash_device_token(token),
        "expires_at": expires_at,
        "used": 0,
        "created_by_user": frappe.session.user,
    }).insert(ignore_permissions=True)

    _audit_device_action(frappe.session.user, None, pos.name, "enrollment_code_issued")

    site_url = frappe.utils.get_url()
    enrollment_value = json.dumps(
        {"url": site_url, "token": token}, separators=(",", ":")
    )
    return {
        "url": site_url,
        "token": token,
        "pos_profile": pos.name,
        "expires_at": str(expires_at),
        "enrollment_value": enrollment_value,
        "qr_code": _device_enrollment_qr_data_uri(enrollment_value),
    }


@frappe.whitelist(allow_guest=True)
def redeem_device_enrollment(token, hardware_id):
    """Called by a newly-installed Android device with no ERPNext session yet.
    Single-use, expiring, row-locked exactly like consume_pos_admin_authorization."""
    if not token or not hardware_id:
        frappe.throw(_("Enrollment code and hardware ID are required"))

    token_hash = _hash_device_token(token)
    enrollment_name = frappe.db.get_value(
        "POS Device Enrollment", {"token_hash": token_hash}, "name"
    )
    if not enrollment_name:
        _audit_device_action(None, hardware_id, None, "invalid_code")
        frappe.throw(_("Enrollment code is invalid"), frappe.PermissionError)

    frappe.db.sql(
        "SELECT name FROM `tabPOS Device Enrollment` WHERE name = %s FOR UPDATE",
        (enrollment_name,),
    )
    enrollment = frappe.get_doc("POS Device Enrollment", enrollment_name)

    if cint(enrollment.used):
        _audit_device_action(None, hardware_id, enrollment.pos_profile, "code_already_used")
        frappe.throw(_("Enrollment code has already been used"), frappe.PermissionError)

    if now_datetime() > get_datetime(enrollment.expires_at):
        _audit_device_action(None, hardware_id, enrollment.pos_profile, "code_expired")
        frappe.throw(_("Enrollment code has expired"), frappe.PermissionError)

    enrollment.used = 1
    enrollment.hardware_id = hardware_id
    enrollment.save(ignore_permissions=True)

    device_token = secrets.token_urlsafe(32)
    device_token_hash = _hash_device_token(device_token)
    existing = frappe.db.exists("POS Device", hardware_id)
    if existing:
        frappe.db.set_value("POS Device", hardware_id, {
            "pos_profile": enrollment.pos_profile,
            "device_token_hash": device_token_hash,
            "enabled": 1,
            "enrolled_at": now_datetime(),
            "enrolled_by": enrollment.created_by_user,
            "disabled_at": None,
            "disabled_reason": None,
        })
    else:
        frappe.get_doc({
            "doctype": "POS Device",
            "hardware_id": hardware_id,
            "pos_profile": enrollment.pos_profile,
            "device_token_hash": device_token_hash,
            "enabled": 1,
            "enrolled_at": now_datetime(),
            "enrolled_by": enrollment.created_by_user,
        }).insert(ignore_permissions=True)
    frappe.db.commit()

    _audit_device_action(enrollment.created_by_user, hardware_id, enrollment.pos_profile, "enrolled")

    pos = frappe.get_cached_doc("POS Profile", enrollment.pos_profile)
    return {
        "pos_profile": pos.name,
        "terminal_id": getattr(pos, "custom_terminal_id", "") or "",
        "branch": getattr(pos, "custom_branch", "") or getattr(pos, "branch", "") or "",
        "warehouse": pos.warehouse,
        "oauth_client_id": _get_pos_android_oauth_client_id(),
        "device_token": device_token,
    }


# ---------------------------------------------------------------------------
# POS cashier authentication
# ---------------------------------------------------------------------------

def _audit_cashier_login(user, terminal_id, pos_profile, status, offline_expires_at=None):
    if user and not frappe.db.exists("User", user):
        user = None
    if pos_profile and not frappe.db.exists("POS Profile", pos_profile):
        pos_profile = None

    frappe.get_doc({
        "doctype": "POS Cashier Login Log",
        "user": user,
        "terminal_id": terminal_id,
        "pos_profile": pos_profile,
        "status": status,
        "offline_expires_at": offline_expires_at,
        "created_at": now_datetime(),
    }).insert(ignore_permissions=True)


@frappe.whitelist(allow_guest=False)
def pos_cashier_login(username, password, terminal_id, pos_profile):
    """Verify a cashier's credentials for the Electron terminal.

    Called by an already-authenticated terminal (its own persistent API
    session) to check a specific cashier in at the register.  This does NOT
    switch or create a Frappe session for the cashier — it only verifies
    their credentials via Frappe's password check (never a manual hash
    compare), confirms they hold a POS role, and confirms they are permitted
    on the requested POS Profile.  Every attempt is written to POS Cashier
    Login Log; the password is never logged.
    """
    _require_login()

    if not username or not password or not terminal_id or not pos_profile:
        frappe.throw(_("username, password, terminal_id and pos_profile are required"))

    if not frappe.db.exists("User", username):
        _audit_cashier_login(None, terminal_id, pos_profile, "invalid_user")
        frappe.throw(_("Invalid cashier credentials"), frappe.AuthenticationError)

    if not cint(frappe.db.get_value("User", username, "enabled")):
        _audit_cashier_login(username, terminal_id, pos_profile, "disabled_user")
        frappe.throw(_("Invalid cashier credentials"), frappe.AuthenticationError)

    # Frappe-native password verification. Never compare hashes manually.
    try:
        check_password(username, password)
    except Exception:
        _audit_cashier_login(username, terminal_id, pos_profile, "failed")
        frappe.throw(_("Invalid cashier credentials"), frappe.AuthenticationError)

    roles = set(frappe.get_roles(username)) - {"All", "Guest"}
    if not roles.intersection(_ALLOWED_CASHIER_ROLES):
        _audit_cashier_login(username, terminal_id, pos_profile, "missing_role")
        frappe.throw(_("Cashier is not authorized for POS operations"), frappe.PermissionError)

    if not frappe.db.exists("POS Profile", pos_profile):
        _audit_cashier_login(username, terminal_id, pos_profile, "invalid_pos_profile")
        frappe.throw(_("Invalid POS Profile: {0}").format(pos_profile))

    pos = frappe.get_cached_doc("POS Profile", pos_profile)
    if cint(pos.disabled):
        _audit_cashier_login(username, terminal_id, pos.name, "pos_profile_disabled")
        frappe.throw(_("POS Profile {0} is disabled").format(pos.name))

    user_list = [r.user for r in (pos.get("applicable_for_users") or [])]
    if user_list and username not in user_list:
        _audit_cashier_login(username, terminal_id, pos.name, "pos_profile_not_allowed")
        frappe.throw(
            _("Cashier {0} is not permitted on POS Profile {1}").format(username, pos.name),
            frappe.PermissionError,
        )

    can_start_shift = True  # gated on _ALLOWED_CASHIER_ROLES above
    can_offline_sale = True  # gated on _ALLOWED_CASHIER_ROLES above
    can_refund = bool(roles.intersection(_ALLOWED_REFUND_ROLES))
    can_close_shift = bool(roles.intersection(_ALLOWED_CLOSE_SHIFT_ROLES))
    # Whether this session can void a cart line without a supervisor
    # step-up prompt - same role set authorize_pos_admin_action itself
    # requires, so a supervisor never has to authorize themselves.
    can_void_items = bool(roles.intersection(_ALLOWED_POS_ADMIN_ROLES))

    offline_expires_at = add_to_date(now_datetime(), days=_CASHIER_OFFLINE_LOGIN_VALID_DAYS)

    _audit_cashier_login(username, terminal_id, pos.name, "success", offline_expires_at)

    return {
        "success": True,
        "user": username,
        "full_name": frappe.db.get_value("User", username, "full_name") or username,
        "roles": sorted(roles),
        "allowed_pos_profiles": [pos.name],
        "default_pos_profile": pos.name,
        "can_start_shift": can_start_shift,
        "can_refund": can_refund,
        "can_close_shift": can_close_shift,
        "can_void_items": can_void_items,
        "can_offline_sale": can_offline_sale,
        "offline_login_expires_at": offline_expires_at.isoformat(),
        "require_pin_setup": True,
    }


@frappe.whitelist()
def get_cashier_context(pos_profile, hardware_id):
    """The Bearer-mode (Android OAuth) analogue of pos_cashier_login.

    No password is involved — the request is already Bearer-authenticated
    (Frappe core resolves frappe.session.user before this runs), so identity
    comes from the session, not a client-supplied username/password. Returns
    the same response shape as pos_cashier_login so mobile.ts's existing
    offline-PIN-cache logic doesn't need to change.
    """
    _require_login()

    if not pos_profile or not hardware_id:
        frappe.throw(_("pos_profile and hardware_id are required"))

    bound_profile = require_active_device(hardware_id)
    if bound_profile != pos_profile:
        frappe.throw(
            _("This device is not enrolled for POS Profile {0}").format(pos_profile),
            frappe.PermissionError,
        )

    user = frappe.session.user
    pos = _load_pos_profile(pos_profile)  # enforces applicable_for_users membership for the session user

    roles = set(frappe.get_roles(user)) - {"All", "Guest"}
    if not roles.intersection(_ALLOWED_CASHIER_ROLES):
        _audit_cashier_login(user, hardware_id, pos.name, "missing_role")
        frappe.throw(_("Cashier is not authorized for POS operations"), frappe.PermissionError)

    can_refund = bool(roles.intersection(_ALLOWED_REFUND_ROLES))
    can_close_shift = bool(roles.intersection(_ALLOWED_CLOSE_SHIFT_ROLES))
    can_void_items = bool(roles.intersection(_ALLOWED_POS_ADMIN_ROLES))
    offline_expires_at = add_to_date(now_datetime(), days=_CASHIER_OFFLINE_LOGIN_VALID_DAYS)

    _audit_cashier_login(user, hardware_id, pos.name, "success", offline_expires_at)

    return {
        "success": True,
        "user": user,
        "full_name": frappe.db.get_value("User", user, "full_name") or user,
        "roles": sorted(roles),
        "allowed_pos_profiles": [pos.name],
        "default_pos_profile": pos.name,
        "can_start_shift": True,
        "can_refund": can_refund,
        "can_close_shift": can_close_shift,
        "can_void_items": can_void_items,
        "can_offline_sale": True,
        "offline_login_expires_at": offline_expires_at.isoformat(),
        "require_pin_setup": True,
    }


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
        "update_stock": cint(pos.get("update_stock")),
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
def get_terminal_resource(doctype, name):
    """Single-document master-data read for the POS terminal client, in place
    of Frappe's generic GET /api/resource/<doctype>/<name>.

    Core ERPNext's own DocPerm for doctypes like POS Profile/Company/Coupon
    Code/Customer grants nothing to POS User/POS Supervisor - those roles are
    meant to go through the native POS Awesome page's permission-bypassing
    RPCs, not raw doctype reads. This terminal client used to call the raw
    REST endpoint directly, which 403'd for a cashier with only POS
    User/POS Supervisor until aimatic/fixtures/custom_docperm.json's second
    batch (2026-07-19) widened those roles' Desk-wide read access as an
    interim fix. This endpoint is the intended, narrower replacement - same
    idea as get_item_barcodes/get_uom_conversions above: read via a reviewable,
    explicitly allowlisted method instead of a Desk-wide permission grant. The
    Custom DocPerm grants are left in place as a safety net for terminals
    still running an older client build that calls /api/resource directly;
    see the offline-pos skill for the planned cleanup once the fleet is
    confirmed on a client version built after this endpoint existed.
    """
    _require_login()
    if doctype not in _TERMINAL_MASTER_DATA_DOCTYPES:
        frappe.throw(_("This terminal is not permitted to read {0}").format(doctype), frappe.PermissionError)
    return frappe.get_doc(doctype, name).as_dict()


@frappe.whitelist()
def list_terminal_resources(doctype, fields=None, filters=None, limit_start=0, limit_page_length=500):
    """Paged master-data list read for the POS terminal client, in place of
    Frappe's generic GET /api/resource/<doctype>?fields=...&filters=....
    See get_terminal_resource above for why this exists.
    """
    _require_login()
    if doctype not in _TERMINAL_MASTER_DATA_DOCTYPES:
        frappe.throw(_("This terminal is not permitted to list {0}").format(doctype), frappe.PermissionError)

    if isinstance(fields, str):
        fields = json.loads(fields)
    if isinstance(filters, str):
        filters = json.loads(filters)

    try:
        start = int(limit_start or 0)
        page_length = min(int(limit_page_length or 500), _MAX_PAGE_SIZE)
    except Exception:
        start = 0
        page_length = 500

    return frappe.get_list(
        doctype,
        fields=fields or ["name"],
        filters=filters or {},
        limit_start=start,
        limit_page_length=page_length,
        order_by="modified asc",
        ignore_permissions=True,
    )


@frappe.whitelist(methods=["POST"])
def create_walkin_customer(customer_name, customer_group=None, territory=None,
                            mobile_no=None, email_id=None, tax_id=None, default_price_list=None):
    """Create a walk-in Customer from the terminal, in place of a raw POST to
    /api/resource/Customer (core DocPerm grants POS User/POS Supervisor no
    create access there either - see get_terminal_resource above). Duplicate
    normalized-mobile rejection is enforced by
    customer_validation.validate_customer's own Customer.validate hook, not
    reimplemented here.
    """
    _require_login()
    if not customer_name:
        frappe.throw(_("Customer Name is required."))

    doc = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": territory,
        "mobile_no": mobile_no,
        "email_id": email_id,
        "tax_id": tax_id,
    })
    if default_price_list:
        doc.default_price_list = default_price_list
    doc.insert(ignore_permissions=True)
    return doc.as_dict()


@frappe.whitelist()
def diagnose_terminal_permissions():
    """Structured pass/fail for the current session's access to every
    doctype the terminal client needs, for the Settings screen to surface
    directly (e.g. "Customer: FAIL") instead of the generic "POS Profile
    load failed" error this bug used to produce.

    Checks frappe.has_permission (the raw /api/resource-relevant check), not
    just whether get_terminal_resource's allowlist would let the call
    through - a terminal still running a client build from before that
    endpoint existed hits raw REST directly, so this diagnostic reflects
    that path too, not only the newer RPC path.
    """
    _require_login()
    doctypes = sorted(_TERMINAL_MASTER_DATA_DOCTYPES)
    results = {dt: bool(frappe.has_permission(dt, ptype="read")) for dt in doctypes}
    results["Customer (create)"] = bool(frappe.has_permission("Customer", ptype="create"))
    failures = [k for k, v in results.items() if not v]

    return {
        "user": frappe.session.user,
        "roles": frappe.get_roles(),
        "results": results,
        "all_ok": not failures,
        "failures": failures,
    }


@frappe.whitelist()
def preview_cart(
    pos_profile,
    customer,
    items,
    coupon_code=None,
    redeem_loyalty_points=0,
    loyalty_points=0,
    gift_voucher_code=None,
):
    """Build an unsaved POS Invoice and return priced/taxed cart preview.

    Never inserts, saves, submits, calls FBR servers, updates coupon counts,
    creates loyalty ledger entries, or redeems a gift voucher — gift_voucher_code
    here is validated/priced only, exactly like redeem_loyalty_points is priced
    without being finalized.
    """
    _require_login()
    _enforce_bearer_profile(pos_profile)

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

    payable_before_voucher = flt(
        flt(doc.grand_total, 2) - flt(getattr(doc, "loyalty_amount", 0) or 0, 2), 2
    )

    gift_voucher_amount = 0.0
    gift_voucher_error = None
    if gift_voucher_code:
        from aimatic.gift_voucher.api import _load_active_gift_voucher

        try:
            gv = _load_active_gift_voucher(gift_voucher_code, cust.name)
            criteria = frappe.get_cached_doc("Gift Voucher Criteria", gv.criteria)
            if payable_before_voucher < flt(criteria.minimum_redemption_value, 2):
                gift_voucher_error = _(
                    "This sale must be at least {0} to redeem this gift voucher"
                ).format(criteria.minimum_redemption_value)
            else:
                gift_voucher_amount = min(
                    flt(gv.amount, 2), max(0.0, payable_before_voucher)
                )
        except frappe.ValidationError as e:
            # Preview tolerates an invalid/incomplete code (cashier may still be
            # typing it) — surface the reason instead of hard-failing the cart.
            gift_voucher_error = str(e)

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
        "gift_voucher_amount": gift_voucher_amount,
        "gift_voucher_error": gift_voucher_error,
        "amount_due": flt(payable_before_voucher - gift_voucher_amount, 2),
    }

    return {"rows": rows, "taxes": taxes, "totals": totals}


_OFFLINE_AUTH_METHOD_PIN = "cashier_pin"


@frappe.whitelist()
def submit_online_sale(
    terminal_invoice_id,
    terminal_id,
    pos_profile,
    opening_entry,
    customer,
    items,
    payments,
    cashier_user,
    coupon_code=None,
    redeem_loyalty_points=0,
    loyalty_points=0,
    gift_voucher_code=None,
    cashier_full_name=None,
    offline_authenticated=0,
    offline_auth_method=None,
    local_offline_session_id=None,
    hardware_id=None,
):
    """Create and submit a POS Invoice from the Electron terminal.

    Idempotent: if a POS Invoice with the same terminal_invoice_id already exists
    it is returned immediately without creating a duplicate.  This protects
    against Electron losing the HTTP response after the invoice was created,
    and lets queued offline sales be safely retried/replayed on reconnect
    without ever double-submitting.  An idempotent replay is never re-validated
    against the checks below, so a cashier who was disabled after a sale
    already succeeded cannot retroactively block Electron from confirming it.

    All totals, pricing rules, taxes and payable amounts are recalculated on the
    server.  Values sent by Electron are never trusted.

    cashier_user is the human cashier who rang up the sale, established by
    pos_cashier_login (possibly hours or days earlier if the terminal was
    offline) — never the terminal's own authenticated API identity.  It is
    re-validated against current server state on every non-idempotent call:
    still enabled, still permitted on this POS Profile, and opening_entry
    must actually belong to this cashier.  An offline cache can go stale, so
    none of this is trusted from Electron without a fresh check.

    offline_authenticated sales must report offline_auth_method exactly as
    "cashier_pin" — a locally cached full account password is not an
    acceptable offline credential and is rejected.

    FBR snapshot and accounting rows are applied by the existing validate hook
    (validate_pos_invoice) during doc.insert().  The FBR API call happens through
    the existing before_submit hook (before_submit_pos_invoice) during doc.submit().
    Neither hook is called manually here.

    gift_voucher_code, if provided, is validated server-side (ownership, status,
    expiry, and a minimum-sale-value rule specific to the bracket that issued it)
    and applied as a "Gift Voucher" payment row for the min(voucher amount, amount
    due) — any excess over the bill is forfeited. The voucher is only marked
    Redeemed after doc.submit() succeeds, under a row lock re-checked against
    concurrent redemption of the same code.
    """
    _require_login()
    is_bearer = _is_bearer_authenticated_request()

    if not frappe.has_permission("POS Invoice", "create"):
        frappe.throw(_("Not permitted to create POS Invoice"), frappe.PermissionError)

    if not terminal_invoice_id:
        frappe.throw(_("terminal_invoice_id is required"))

    if not cashier_user and not is_bearer:
        frappe.throw(_("cashier_user is required"), frappe.PermissionError)

    if cint(offline_authenticated) and offline_auth_method != _OFFLINE_AUTH_METHOD_PIN:
        frappe.throw(
            _(
                "Offline-authenticated sales must use offline_auth_method '{0}'; "
                "got '{1}'"
            ).format(_OFFLINE_AUTH_METHOD_PIN, offline_auth_method),
            frappe.PermissionError,
        )

    # Idempotency: return existing invoice if one was already created for this terminal request.
    # Deliberately checked before cashier/device resolution below — a device that was
    # disabled *after* successfully creating this invoice must still be able to get an
    # idempotent confirmation of it (see offline queue replay guarantees).
    existing_name = _find_existing_invoice(terminal_invoice_id)
    if existing_name:
        return _build_submission_response(frappe.get_doc("POS Invoice", existing_name))

    # Auth and profile checks
    pos = _get_pos_profile_or_throw(pos_profile)
    cashier_user = _resolve_cashier_user(cashier_user, pos_profile, hardware_id, is_bearer)
    _validate_cashier_for_sale(cashier_user, pos)
    _require_open_entry_for_cashier(opening_entry, cashier_user, pos)
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

    # Gift voucher redemption is a payment-side concern, applied after FBR
    # accounting so it never touches grand_total / the FBR payload — see
    # aimatic.gift_voucher for why this must be a Mode of Payment row and not
    # a discount. Loaded here (not earlier) so it reflects any loyalty
    # redemption already folded into doc.loyalty_amount above.
    gift_voucher_doc = None
    gift_voucher_amount = 0.0
    if gift_voucher_code:
        from aimatic.gift_voucher.api import _load_active_gift_voucher

        gift_voucher_doc = _load_active_gift_voucher(gift_voucher_code, cust.name)
        criteria = frappe.get_cached_doc("Gift Voucher Criteria", gift_voucher_doc.criteria)
        payable_before_voucher = flt(
            flt(doc.grand_total, 2) - flt(getattr(doc, "loyalty_amount", 0) or 0, 2), 2
        )
        if payable_before_voucher < flt(criteria.minimum_redemption_value, 2):
            frappe.throw(
                _("This sale must be at least {0} to redeem this gift voucher").format(
                    criteria.minimum_redemption_value
                )
            )
        # Excess voucher value over the bill is forfeited, not carried forward.
        gift_voucher_amount = min(flt(gift_voucher_doc.amount, 2), max(0.0, payable_before_voucher))

    # Validate the client-supplied payments against the server-computed grand_total
    # and write them onto the doc.
    _validate_and_set_payments(doc, pos, payments, gift_voucher_amount=gift_voucher_amount)

    if gift_voucher_doc and gift_voucher_amount > 0:
        # Appended after _validate_and_set_payments, which resets doc.payments to
        # only the client-sent rows — this one is server-only and must never be
        # sent/chosen by the terminal (the Mode of Payment isn't in any POS
        # Profile's allowed list, so a client-sent row for it would be rejected).
        doc.append("payments", {"mode_of_payment": "Gift Voucher", "amount": gift_voucher_amount})

    # Stamp terminal identifiers before insert so they are stored with the record
    _set_terminal_fields(doc, terminal_invoice_id, terminal_id, hardware_id)
    _set_cashier_offline_fields(
        doc,
        cashier_user,
        cashier_full_name,
        offline_authenticated,
        offline_auth_method,
        local_offline_session_id,
    )

    # Insert + submit within a named savepoint so any unexpected failure rolls back
    # cleanly without aborting the outer transaction.
    sp = "submit_online_sale"
    frappe.db.savepoint(sp)
    try:
        doc.insert()   # validate hook fires: FBR snapshot + accounting rows (idempotent)
        doc.submit()   # before_submit hook fires: FBR API call
        # ERPNext's POS Closing Entry attributes shift invoices by the `owner`
        # metadata field (see build_invoice_query in pos_closing_entry.py), not
        # by any cashier-specific business field.  The invoice was inserted
        # under the terminal's own authenticated session, so owner defaults to
        # the terminal — reattribute it to the actual cashier so end-of-shift
        # closing/reconciliation finds this invoice under the right shift.
        frappe.db.set_value("POS Invoice", doc.name, "owner", cashier_user, update_modified=False)
        doc.owner = cashier_user

        if gift_voucher_doc and gift_voucher_amount > 0:
            # Lock the voucher row and re-check it's still Active before marking
            # it Redeemed, so a concurrent redemption of the same code loses this
            # race cleanly (raises here, rolling back the whole submission)
            # rather than double-spending the voucher.
            frappe.db.sql(
                "SELECT name FROM `tabGift Voucher` WHERE name = %s FOR UPDATE",
                (gift_voucher_doc.name,),
            )
            current_status = frappe.db.get_value("Gift Voucher", gift_voucher_doc.name, "status")
            if current_status != "Active":
                frappe.throw(_("This gift voucher is no longer available for redemption"))
            frappe.db.set_value(
                "Gift Voucher",
                gift_voucher_doc.name,
                {
                    "status": "Redeemed",
                    "redeemed_against_invoice": doc.name,
                    "redeemed_on": now_datetime(),
                },
            )

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


def _validate_and_set_payments(doc, pos, payments_data, gift_voucher_amount=0):
    """Validate client payment rows and write them onto the doc.

    Rules enforced:
    - Every mode_of_payment must be in the POS Profile's allowed list.
    - Every amount must be positive.
    - Non-cash payments cannot individually exceed the remaining payable amount.
    - Cash may exceed payable (creates change).
    - Total payments must cover payable_after_loyalty_and_gift_voucher.

    gift_voucher_amount is already-validated server-side (see submit_online_sale)
    and reduces payable the same way loyalty_amount does; it is never part of
    payments_data since the client can't choose/send the Gift Voucher mode.
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
    payable = flt(grand_total - loyalty_amount - flt(gift_voucher_amount, 2), 2)

    total_paid = 0.0

    for p in payments_data:
        mode = (p.get("mode_of_payment") or "").strip()
        amount = flt(p.get("amount") or 0, 2)

        if not mode:
            frappe.throw(_("Each payment row must have mode_of_payment"))

        if mode == "Gift Voucher":
            # "Gift Voucher" is a server-only mode this function itself
            # appends via gift_voucher_amount - never something a client can
            # submit as one of its own payment rows, regardless of what a
            # misconfigured POS Profile's payment list happens to allow (a
            # real incident: siezal's POS Profiles once had it configured
            # there, which would have let this exact check silently accept a
            # cashier-typed "Gift Voucher" payment of any amount backed by no
            # real voucher). Reject unconditionally rather than trusting
            # allowed_modes alone.
            frappe.throw(
                _(
                    "'Gift Voucher' is not a selectable payment mode - redeem a "
                    "gift voucher via its code instead."
                )
            )

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


def _validate_cashier_for_sale(cashier_user, pos):
    """Re-validate a cashier at sale time; offline caches can go stale.

    Raises frappe.PermissionError with a clear message if the cashier is no
    longer enabled or no longer permitted on this POS Profile.
    """
    if not frappe.db.exists("User", cashier_user):
        frappe.throw(
            _("Cashier {0} is not a valid user").format(cashier_user), frappe.PermissionError
        )

    if not cint(frappe.db.get_value("User", cashier_user, "enabled")):
        frappe.throw(
            _("Cashier {0} is disabled and cannot submit sales").format(cashier_user),
            frappe.PermissionError,
        )

    user_list = [r.user for r in (pos.get("applicable_for_users") or [])]
    if user_list and cashier_user not in user_list:
        frappe.throw(
            _("Cashier {0} is not permitted on POS Profile {1}").format(cashier_user, pos.name),
            frappe.PermissionError,
        )


def _set_terminal_fields(doc, terminal_invoice_id, terminal_id, hardware_id=None):
    meta = doc.meta
    if meta.has_field("custom_terminal_invoice_id"):
        doc.custom_terminal_invoice_id = terminal_invoice_id
    if meta.has_field("custom_terminal_id"):
        doc.custom_terminal_id = terminal_id or ""
    if meta.has_field("custom_hardware_id"):
        doc.custom_hardware_id = hardware_id or ""


def _set_cashier_offline_fields(
    doc,
    cashier_user,
    cashier_full_name,
    offline_authenticated,
    offline_auth_method,
    local_offline_session_id,
):
    meta = doc.meta
    if meta.has_field("custom_cashier_user"):
        doc.custom_cashier_user = cashier_user or ""
    if meta.has_field("custom_cashier_full_name"):
        doc.custom_cashier_full_name = cashier_full_name or ""
    if meta.has_field("custom_offline_authenticated"):
        doc.custom_offline_authenticated = cint(offline_authenticated)
    if meta.has_field("custom_offline_auth_method"):
        doc.custom_offline_auth_method = offline_auth_method or ""
    if meta.has_field("custom_local_offline_session_id"):
        doc.custom_local_offline_session_id = local_offline_session_id or ""


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
        "cashier_user": _cf("custom_cashier_user"),
        "cashier_full_name": _cf("custom_cashier_full_name"),
        "offline_authenticated": bool(cint(_cf("custom_offline_authenticated") or 0)),
    }


@frappe.whitelist()
def get_customer_benefits(pos_profile, customer):
    """Return loyalty program details for a customer using the POS Profile company.

    Returns zero/None values when the customer is not enrolled.
    """
    _require_login()
    _enforce_bearer_profile(pos_profile)

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
def get_active_pos_session(pos_profile, cashier_user):
    """Return the active POS Opening Entry for a specific cashier + POS Profile.

    Looked up by cashier_user, never by the authenticated (terminal) session
    user — a terminal is shared across cashiers, so "my session" is not a
    meaningful concept here.  If a different cashier currently has this POS
    Profile's shift open, that is surfaced under other_open_sessions as
    diagnostic info only; it is never silently reused for a different
    cashier_user.
    """
    _require_login()
    is_bearer = _is_bearer_authenticated_request()

    if not pos_profile:
        frappe.throw(_("pos_profile is required"))
    if not cashier_user and not is_bearer:
        frappe.throw(_("cashier_user is required"))
    cashier_user = _resolve_cashier_user(cashier_user, pos_profile, None, is_bearer)

    # All submitted open entries for this profile — for the match and diagnostics
    profile_open = frappe.get_all(
        "POS Opening Entry",
        filters={"pos_profile": pos_profile, "docstatus": 1, "status": "Open"},
        fields=["name", "user", "pos_profile", "company", "status", "docstatus", "period_start_date"],
        order_by="period_start_date desc",
    )

    matched = next((e for e in profile_open if e.user == cashier_user), None)

    if matched:
        return {
            "opening_entry": matched.name,
            "user": matched.user,
            "cashier_user": matched.user,
            "cashier_full_name": frappe.db.get_value("User", matched.user, "full_name") or matched.user,
            "pos_profile": matched.pos_profile,
            "company": matched.company,
            "status": matched.status,
            "docstatus": matched.docstatus,
            "period_start_date": matched.period_start_date,
            "reason": "Active session found",
            "other_open_sessions": [],
        }

    other_open = [
        {
            "opening_entry": e.name,
            "user": e.user,
            "cashier_full_name": frappe.db.get_value("User", e.user, "full_name") or e.user,
            "period_start_date": e.period_start_date,
        }
        for e in profile_open
    ]

    return {
        "opening_entry": None,
        "user": None,
        "cashier_user": cashier_user,
        "cashier_full_name": frappe.db.get_value("User", cashier_user, "full_name") or cashier_user,
        "pos_profile": pos_profile,
        "company": None,
        "status": None,
        "docstatus": None,
        "period_start_date": None,
        "reason": (
            "Another cashier has this POS Profile open"
            if other_open
            else "No submitted open POS Opening Entry exists for this cashier"
        ),
        "other_open_sessions": other_open,
    }


@frappe.whitelist()
def close_pos_session(opening_entry, cashier_user, closing_balances, notes=None, supervisor_token=None):
    """Create and submit a POS Closing Entry for a specific cashier's shift.

    The authenticated caller is the terminal's own fixed API identity.
    cashier_user must match the Opening Entry's owning cashier. This never
    closes a different cashier's shift, even one open on the same
    terminal/POS Profile.

    cashier_user must hold a close-shift role (POS Supervisor or System
    Manager) OR supply a valid, unused, unexpired supervisor_token minted for
    this POS Profile's terminal via authorize_pos_admin_action(action=
    "close_shift") — a plain POS User can start a shift (see
    start_pos_session) but cannot close one alone; a supervisor must
    authorize it (their own credentials, not the cashier's), without the
    cashier having to log out. The token is consumed here, in the same DB
    transaction as the close itself, so a token is never burned if the close
    subsequently fails/rolls back for an unrelated reason (e.g. a
    misconfigured account elsewhere in the chain).

    The client supplies only its counted amount for each payment mode.  Invoice
    totals, expected amounts, and the opening balance are always derived from
    ERPNext documents on the server.
    """
    _require_login()
    is_bearer = _is_bearer_authenticated_request()
    frappe.has_permission("POS Closing Entry", "create", throw=True)

    if not cashier_user and not is_bearer:
        frappe.throw(_("cashier_user is required"), frappe.PermissionError)

    try:
        opening = frappe.get_doc("POS Opening Entry", opening_entry)
    except frappe.DoesNotExistError:
        frappe.throw(_("Invalid POS Opening Entry: {0}").format(opening_entry))

    cashier_user = _resolve_cashier_user(
        cashier_user, opening.pos_profile, None, is_bearer
    )

    if opening.user != cashier_user:
        frappe.throw(
            _("POS Opening Entry {0} does not belong to cashier {1}").format(
                opening.name, cashier_user
            ),
            frappe.PermissionError,
        )

    pos = _get_pos_profile_or_throw(opening.pos_profile)
    cashier_roles = _validate_cashier_identity(cashier_user, pos, required_roles=_ALLOWED_CASHIER_ROLES)

    if not cashier_roles.intersection(_ALLOWED_CLOSE_SHIFT_ROLES):
        terminal_id = pos.get("custom_terminal_id")
        if not terminal_id:
            frappe.throw(
                _("POS Profile {0} has no Terminal ID assigned - supervisor authorization cannot be verified.").format(
                    pos.name
                )
            )
        if not supervisor_token:
            frappe.throw(
                _("Supervisor authorization is required to close this shift."), frappe.PermissionError
            )
        # Same HTTPS requirement authorize_pos_admin_action enforces when minting
        # the token - consuming it over plain HTTP would defeat that protection.
        _require_https_for_pos_admin_authorization()
        _consume_pos_admin_authorization_token(supervisor_token, action="close_shift", terminal_id=terminal_id)

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

    # ERPNext derives the shift invoices, payment totals, and linked documents,
    # matching invoices to this shift by comparing each invoice's `owner` field
    # to closing.user — so closing.user must be the cashier who owns the shift,
    # never the terminal's own authenticated identity (make_closing_entry_from_opening
    # already sets this from opening_entry.user; kept explicit here since getting
    # this wrong makes every invoice in the shift fail POS Closing Entry submission).
    closing = make_closing_entry_from_opening(opening.as_dict())
    closing.period_end_date = now_datetime()
    closing.posting_date = nowdate()
    closing.user = cashier_user
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
        "cashier_user": closing.user,
        "cashier_full_name": frappe.db.get_value("User", closing.user, "full_name") or closing.user,
        "grand_total": closing.grand_total,
        "net_total": closing.net_total,
        "total_quantity": closing.total_quantity,
        "payment_reconciliation": [row.as_dict() for row in closing.payment_reconciliation],
    }


def _get_open_pos_opening_entry_for_cashier(opening_entry, cashier_user):
    if not opening_entry:
        frappe.throw(_("opening_entry is required"))
    if not cashier_user:
        frappe.throw(_("cashier_user is required"), frappe.PermissionError)

    doc = frappe.get_doc("POS Opening Entry", opening_entry)
    doc.check_permission("read")

    if doc.docstatus != 1 or doc.status != "Open":
        frappe.throw(_("POS Opening Entry {0} is not open").format(opening_entry))

    if doc.user != cashier_user:
        frappe.throw(
            _("POS Opening Entry {0} does not belong to cashier {1}").format(
                opening_entry, cashier_user
            ),
            frappe.PermissionError,
        )

    return doc


@frappe.whitelist()
def get_pos_closing_summary(opening_entry, cashier_user):
    """Preview end-of-shift totals for a specific cashier's open shift.

    Callable by the terminal's own authenticated API identity, but
    opening_entry must actually belong to cashier_user — never inferred from
    the authenticated session, and never computed across a different
    cashier's invoices.
    """
    _require_login()
    is_bearer = _is_bearer_authenticated_request()

    opening_profile = frappe.db.get_value("POS Opening Entry", opening_entry, "pos_profile")
    if not opening_profile:
        frappe.throw(_("Invalid POS Opening Entry: {0}").format(opening_entry))
    cashier_user = _resolve_cashier_user(
        cashier_user, opening_profile, None, is_bearer
    )

    opening = _get_open_pos_opening_entry_for_cashier(opening_entry, cashier_user)

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
        "cashierUser": closing_entry.user,
        "cashierFullName": frappe.db.get_value("User", closing_entry.user, "full_name")
        or closing_entry.user,
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
def start_pos_session(pos_profile, cashier_user, opening_balances=None):
    """Create and submit a POS Opening Entry for a human cashier.

    The authenticated caller is the terminal's own fixed API identity;
    cashier_user is the human cashier who will own the shift.  cashier_user is
    validated independently (exists, enabled, holds a POS role, permitted on
    this POS Profile) and the resulting Opening Entry is always created under
    cashier_user — never under the terminal's own authenticated user.

    Idempotent: if cashier_user already has an open submitted Opening Entry
    for this POS Profile + company, it is returned instead of creating a
    duplicate.  This also covers the offline-batch case, where Electron may
    call this repeatedly while replaying a queue of offline sales without a
    real per-shift opening_entry of its own — opening_balances can be omitted
    (defaults to zero for every configured payment mode), and the resulting
    entry is never auto-closed here.

    opening_balances: JSON string or list of {mode_of_payment, opening_amount}.
                      Electron should send this JSON-stringified.
    """
    _require_login()
    is_bearer = _is_bearer_authenticated_request()

    if not frappe.has_permission("POS Opening Entry", "create"):
        frappe.throw(_("Not permitted to create POS Opening Entry"), frappe.PermissionError)
    if not frappe.has_permission("POS Opening Entry", "submit"):
        frappe.throw(_("Not permitted to submit POS Opening Entry"), frappe.PermissionError)

    pos = _get_pos_profile_or_throw(pos_profile)
    cashier_user = _resolve_cashier_user(cashier_user, pos.name, None, is_bearer)
    _validate_cashier_identity(cashier_user, pos)

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

    # Idempotency: return existing open session for this cashier if one already exists
    existing = frappe.get_all(
        "POS Opening Entry",
        filters={
            "pos_profile": pos.name,
            "company": pos.company,
            "user": cashier_user,
            "docstatus": 1,
            "status": "Open",
        },
        fields=["name"],
        order_by="period_start_date desc",
        limit=1,
    )
    if existing:
        return _build_opening_session_response(frappe.get_doc("POS Opening Entry", existing[0].name))

    entry = frappe.new_doc("POS Opening Entry")
    entry.pos_profile = pos.name
    entry.user = cashier_user
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

    return _build_opening_session_response(entry)


def _build_opening_session_response(entry):
    """Serialize a POS Opening Entry, surfacing the owning cashier explicitly."""
    data = entry.as_dict()
    data["cashier_user"] = entry.user
    data["cashier_full_name"] = frappe.db.get_value("User", entry.user, "full_name") or entry.user
    return data


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


def _require_refund_permission(pos, user=None):
    """Require a refund role, or an explicit POS Profile user allow-list entry.

    user defaults to the authenticated session user; callers acting on behalf
    of a separate human cashier (e.g. submit_pos_refund) pass cashier_user
    explicitly so the check runs against the cashier, not the terminal.
    """
    user = user or frappe.session.user
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


def _validate_refund_quantities(original, requested):
    """Reject zero/negative or over-remaining requested quantities (re-checkable inside a txn)."""
    returned = returned_qty_by_row(original.name)
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
        "cashier_user": _cf("custom_cashier_user"),
        "cashier_full_name": _cf("custom_cashier_full_name"),
        "offline_authenticated": bool(cint(_cf("custom_offline_authenticated") or 0)),
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
    _enforce_bearer_profile(original.pos_profile)

    if original.docstatus != 1:
        frappe.throw(_("Original POS Invoice must be submitted"))
    if cint(getattr(original, "is_return", 0)):
        frappe.throw(_("Cannot refund a return invoice"))

    returned = returned_qty_by_row(original.name)

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
    cashier_user,
    pos_opening_entry=None,
    reason=None,
    items=None,
    payments=None,
    cashier_full_name=None,
    offline_authenticated=0,
    offline_auth_method=None,
    local_offline_session_id=None,
    hardware_id=None,
):
    """Create and submit a return POS Invoice against an original sale.

    Idempotent on custom_terminal_refund_id. Reuses the standard ERPNext return
    mapper for original rates/accounts, restricts it to the requested quantities,
    and lets the existing FBR hooks build the InvoiceType=2 credit note and submit
    to FBR exactly once. Electron never calls FBR. The Rs.1 POS service fee is
    neither added nor refunded on returns (see fbr_pos.accounting).

    cashier_user is the human cashier processing the refund — never the
    terminal's own authenticated API identity — validated the same way
    submit_online_sale validates its cashier: must be enabled, permitted on
    this POS Profile, and (via _require_refund_permission) either hold a
    refund role or be explicitly allow-listed on the POS Profile. If
    pos_opening_entry is given it must belong to cashier_user; otherwise
    cashier_user must have some other open shift on this POS Profile. The
    return invoice's `owner` is reattributed to cashier_user after submit,
    for the same reason submit_online_sale does this: ERPNext's POS Closing
    Entry matches shift invoices by `owner`, not by any cashier-specific
    business field, so an unattributed refund would be invisible to that
    cashier's shift close/reconciliation.
    """
    _require_login()
    is_bearer = _is_bearer_authenticated_request()

    if not frappe.has_permission("POS Invoice", "create"):
        frappe.throw(_("Not permitted to create POS Invoice"), frappe.PermissionError)
    if not frappe.has_permission("POS Invoice", "submit"):
        frappe.throw(_("Not permitted to submit POS Invoice"), frappe.PermissionError)
    if not terminal_refund_id:
        frappe.throw(_("terminal_refund_id is required"))
    if not cashier_user and not is_bearer:
        frappe.throw(_("cashier_user is required"), frappe.PermissionError)

    if cint(offline_authenticated) and offline_auth_method != _OFFLINE_AUTH_METHOD_PIN:
        frappe.throw(
            _(
                "Offline-authenticated refunds must use offline_auth_method '{0}'; "
                "got '{1}'"
            ).format(_OFFLINE_AUTH_METHOD_PIN, offline_auth_method),
            frappe.PermissionError,
        )

    # Idempotency: never create a second return for the same refund UUID. Deliberately
    # checked before cashier/device resolution, same reasoning as submit_online_sale.
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

    # Cashier identity, refund authorization, and shift checks — all against
    # cashier_user, never the terminal's own authenticated session.
    pos = _get_pos_profile_or_throw(original.pos_profile)
    cashier_user = _resolve_cashier_user(cashier_user, original.pos_profile, hardware_id, is_bearer)
    _validate_cashier_for_sale(cashier_user, pos)
    _require_refund_permission(pos, cashier_user)
    if pos_opening_entry:
        _require_open_entry_for_cashier(pos_opening_entry, cashier_user, pos)
    else:
        _validate_pos_opening_entry(pos.name, cashier_user)

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
    if return_doc.meta.has_field("custom_hardware_id"):
        return_doc.custom_hardware_id = getattr(original, "custom_hardware_id", None) or ""
    if reason and return_doc.meta.has_field("remarks"):
        return_doc.remarks = reason

    _set_cashier_offline_fields(
        return_doc,
        cashier_user,
        cashier_full_name,
        offline_authenticated,
        offline_auth_method,
        local_offline_session_id,
    )

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
        # Reattribute from the terminal's session to the cashier — see docstring.
        frappe.db.set_value(
            "POS Invoice", return_doc.name, "owner", cashier_user, update_modified=False
        )
        return_doc.owner = cashier_user
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
