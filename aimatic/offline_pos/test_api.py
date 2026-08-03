"""
Tests for aimatic.offline_pos.api and aimatic.offline_pos.customer_validation.

Run with:
    bench --site <site> run-tests --app aimatic --module aimatic.offline_pos.test_api
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.exceptions import PermissionError as FrappePermissionError
from frappe.tests import IntegrationTestCase

# ---------------------------------------------------------------------------
# Site-specific fixtures (resolved once at module import against the live DB)
# ---------------------------------------------------------------------------

def _site_pos_profile_name():
    row = frappe.db.get_value(
        "POS Profile", {"disabled": 0}, "name", order_by="creation asc"
    )
    return row


def _site_item_code():
    row = frappe.db.get_value(
        "Item", {"disabled": 0, "is_sales_item": 1}, "item_code"
    )
    return row


def _site_customer_name():
    row = frappe.db.get_value("Customer", {"disabled": 0}, "name")
    return row


def _site_stocked_item_code():
    """An active sales item with real stock on hand, for tests that actually
    submit a POS Invoice (submission validates stock; _ITEM_CODE is not
    guaranteed to have any, and preview-only tests don't need it to)."""
    row = frappe.db.sql(
        """
        SELECT bin.item_code
        FROM `tabBin` bin
        INNER JOIN `tabItem` item ON item.item_code = bin.item_code
        WHERE bin.actual_qty > 0
          AND item.disabled = 0
          AND item.is_sales_item = 1
        LIMIT 1
        """
    )
    return row[0][0] if row else None


_POS_PROFILE_NAME = _site_pos_profile_name()
_ITEM_CODE = _site_item_code()
_CUSTOMER_NAME = _site_customer_name()
_STOCKED_ITEM_CODE = _site_stocked_item_code()


def _require_fixtures(test):
    """Skip the test when required fixtures are not present in this site."""
    def wrapper(self):
        if not _POS_PROFILE_NAME:
            raise unittest.SkipTest("No enabled POS Profile found in site")
        if not _ITEM_CODE:
            raise unittest.SkipTest("No active sales item found in site")
        if not _CUSTOMER_NAME:
            raise unittest.SkipTest("No enabled customer found in site")
        test(self)
    wrapper.__name__ = test.__name__
    return wrapper


# ---------------------------------------------------------------------------
# Test base
# ---------------------------------------------------------------------------

class _AimTestCase(IntegrationTestCase):
    """Saves/restores the Frappe session user and rolls back the DB after each test."""

    def setUp(self):
        super().setUp()
        self._original_user = frappe.session.user
        frappe.set_user("Administrator")

    def tearDown(self):
        try:
            frappe.set_user(self._original_user)
            frappe.db.rollback()
        finally:
            super().tearDown()


class TestAndroidBearerSecurity(_AimTestCase):
    def test_bearer_detection_is_false_without_http_request(self):
        from aimatic.offline_pos.api import _is_bearer_authenticated_request

        with patch("aimatic.offline_pos.api.frappe.local", SimpleNamespace()):
            self.assertFalse(_is_bearer_authenticated_request())

    def test_bearer_detection_does_not_confuse_terminal_token(self):
        from aimatic.offline_pos.api import _is_bearer_authenticated_request

        terminal = SimpleNamespace(request=SimpleNamespace(headers={"Authorization": "token key:secret"}))
        oauth = SimpleNamespace(request=SimpleNamespace(headers={"Authorization": "Bearer access-token"}))
        with patch("aimatic.offline_pos.api.frappe.local", terminal):
            self.assertFalse(_is_bearer_authenticated_request())
        with patch("aimatic.offline_pos.api.frappe.local", oauth):
            self.assertTrue(_is_bearer_authenticated_request())

    def test_bearer_cashier_identity_comes_from_session_and_device_profile(self):
        from aimatic.offline_pos.api import _resolve_cashier_user

        frappe.set_user("Administrator")
        with patch("aimatic.offline_pos.api.require_active_device", return_value="Main POS"):
            self.assertEqual(
                _resolve_cashier_user("attacker@example.com", "Main POS", "device-1", True),
                "Administrator",
            )

    def test_public_oauth_client_id_is_resolved_by_app_name(self):
        from aimatic.offline_pos.api import _get_pos_android_oauth_client_id

        with patch("aimatic.offline_pos.api.frappe.db.get_value", return_value="public-client-id") as get_value:
            self.assertEqual(_get_pos_android_oauth_client_id(), "public-client-id")
            get_value.assert_called_once_with(
                "OAuth Client", {"app_name": "Aimatic POS Android"}, "name"
            )

    def test_device_proof_binds_request_to_enabled_device(self):
        from aimatic.aimatic.offline_pos.device_auth import (
            hash_device_token,
            validate_device_proof,
        )

        device = SimpleNamespace(
            enabled=1,
            pos_profile="Main POS",
            device_token_hash=hash_device_token("device-proof"),
        )
        with patch(
            "aimatic.aimatic.offline_pos.device_auth.frappe.db.get_value",
            return_value=device,
        ):
            self.assertEqual(
                validate_device_proof("device-1", "device-proof"),
                "Main POS",
            )

    def test_invalid_device_proof_is_rejected(self):
        from aimatic.aimatic.offline_pos.device_auth import (
            hash_device_token,
            validate_device_proof,
        )

        device = SimpleNamespace(
            enabled=1,
            pos_profile="Main POS",
            device_token_hash=hash_device_token("correct-proof"),
        )
        with (
            patch(
                "aimatic.aimatic.offline_pos.device_auth.frappe.db.get_value",
                return_value=device,
            ),
            patch("aimatic.aimatic.offline_pos.device_auth._audit_device_failure"),
            self.assertRaises(frappe.AuthenticationError),
        ):
            validate_device_proof("device-1", "wrong-proof")

    def test_device_update_audit_includes_required_timestamp(self):
        from aimatic.aimatic.offline_pos.device_events import on_pos_device_update

        doc = SimpleNamespace(
            enabled=0,
            hardware_id="device-1",
            pos_profile="Main POS",
            has_value_changed=lambda field: field == "enabled",
        )
        inserted = {}

        class AuditDoc:
            def insert(self, ignore_permissions=False):
                inserted["ignore_permissions"] = ignore_permissions

        def get_doc(value):
            inserted.update(value)
            return AuditDoc()

        with patch("aimatic.aimatic.offline_pos.device_events.frappe.get_doc", side_effect=get_doc):
            on_pos_device_update(doc, "on_update")

        self.assertEqual(inserted["status"], "device_disabled")
        self.assertTrue(inserted["created_at"])


def _create_opening_entry(pos_profile_name, user="Administrator"):
    """Submit a POS Opening Entry for the given profile and user."""
    pos = frappe.get_doc("POS Profile", pos_profile_name)
    entry = frappe.new_doc("POS Opening Entry")
    entry.pos_profile = pos.name
    entry.user = user
    entry.company = pos.company
    entry.period_start_date = frappe.utils.get_datetime()
    for d in pos.get("payments") or []:
        entry.append("balance_details", {"mode_of_payment": d.mode_of_payment})
    entry.submit()
    return entry


def _make_test_customer(name):
    if frappe.db.exists("Customer", name):
        return frappe.get_doc("Customer", name)
    cg = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    ter = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
    doc = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": name,
        "customer_type": "Individual",
        "customer_group": cg,
        "territory": ter,
    })
    doc.flags.ignore_validate = True
    doc.insert(ignore_permissions=True)
    return doc


def _make_restricted_pos_profile(restrict_to_user):
    """Clone the site's reference POS Profile and restrict it to one user.

    Uses the existing profile's account settings to satisfy mandatory fields.
    Returns the name of the new profile (rolled back in tearDown).
    """
    ref = frappe.get_doc("POS Profile", _POS_PROFILE_NAME)
    new_name = "_Test AIM Restricted POS"

    pos = frappe.get_doc({
        "doctype": "POS Profile",
        "name": new_name,
        "company": ref.company,
        "currency": ref.currency,
        "warehouse": ref.warehouse,
        "selling_price_list": ref.selling_price_list,
        "income_account": ref.income_account,
        "write_off_account": ref.write_off_account,
        "write_off_cost_center": ref.write_off_cost_center,
        "write_off_limit": ref.write_off_limit,
        "cost_center": ref.cost_center,
        "branch": ref.get("branch"),  # mandatory accounting dimension on this site
        # Deterministic, not copied from ref - close_pos_session's supervisor-token
        # path (and authorize_pos_admin_action/consume_pos_admin_authorization)
        # binds a token to this exact value, so tests need a real one.
        "custom_terminal_id": "TEST-TERM-1",
    })
    for d in ref.get("payments") or []:
        pos.append("payments", {"mode_of_payment": d.mode_of_payment, "default": d.default or 0})
    pos.append("applicable_for_users", {"user": restrict_to_user, "default": 1})
    pos.insert(ignore_permissions=True)
    return new_name


# ---------------------------------------------------------------------------
# normalize_pak_mobile — pure unit tests (no DB)
# ---------------------------------------------------------------------------

class TestNormalizePakMobile(unittest.TestCase):
    def _n(self, v):
        from aimatic.offline_pos.customer_validation import normalize_pak_mobile
        return normalize_pak_mobile(v)

    def test_local_leading_zero(self):
        self.assertEqual(self._n("03001234567"), "+923001234567")

    def test_local_with_spaces(self):
        self.assertEqual(self._n("0300 123 4567"), "+923001234567")

    def test_local_with_dashes(self):
        self.assertEqual(self._n("0300-123-4567"), "+923001234567")

    def test_international_without_plus(self):
        self.assertEqual(self._n("923001234567"), "+923001234567")

    def test_international_with_plus(self):
        self.assertEqual(self._n("+923001234567"), "+923001234567")

    def test_leading_0092(self):
        self.assertEqual(self._n("00923001234567"), "+923001234567")

    def test_short_three_prefix(self):
        self.assertEqual(self._n("3001234567"), "+923001234567")

    def test_invalid_returns_none(self):
        self.assertIsNone(self._n("12345"))

    def test_empty_returns_none(self):
        self.assertIsNone(self._n(""))

    def test_none_returns_none(self):
        self.assertIsNone(self._n(None))


# ---------------------------------------------------------------------------
# customer_validation — integration tests
# ---------------------------------------------------------------------------

class TestCustomerValidation(_AimTestCase):

    def test_duplicate_mobile_blocked(self):
        from aimatic.offline_pos.customer_validation import validate_customer

        c1 = _make_test_customer("_CV Dup A")
        c1.mobile_no = "+923009991111"
        c1.flags.ignore_validate = True
        c1.save(ignore_permissions=True)

        cg = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
        ter = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
        c2 = frappe.new_doc("Customer")
        c2.customer_name = "_CV Dup B"
        c2.customer_type = "Individual"
        c2.customer_group = cg
        c2.territory = ter
        c2.mobile_no = "03009991111"  # same number, different format

        with self.assertRaises(frappe.ValidationError):
            validate_customer(c2)

    def test_editing_own_mobile_allowed(self):
        from aimatic.offline_pos.customer_validation import validate_customer

        c = _make_test_customer("_CV Self Edit")
        c.mobile_no = "+923009992222"
        c.flags.ignore_validate = True
        c.save(ignore_permissions=True)

        c.mobile_no = "03009992222"  # same, un-normalized
        validate_customer(c)  # must not raise
        self.assertEqual(c.mobile_no, "+923009992222")

    def test_non_pak_number_left_as_is(self):
        from aimatic.offline_pos.customer_validation import validate_customer

        cg = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
        ter = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
        c = frappe.new_doc("Customer")
        c.customer_name = "_CV Foreign"
        c.customer_type = "Individual"
        c.customer_group = cg
        c.territory = ter
        c.mobile_no = "0045700000001"  # Danish — not Pakistani
        validate_customer(c)  # must not raise
        self.assertEqual(c.mobile_no, "0045700000001")

    def test_default_price_list_set_when_empty(self):
        from aimatic.offline_pos.customer_validation import validate_customer

        selling = frappe.get_single("Selling Settings")
        if not selling.selling_price_list:
            pl = frappe.db.get_value("Price List", {"selling": 1}, "name")
            selling.selling_price_list = pl
            selling.save(ignore_permissions=True)

        cg = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
        ter = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
        c = frappe.new_doc("Customer")
        c.customer_name = "_CV PL Default"
        c.customer_type = "Individual"
        c.customer_group = cg
        c.territory = ter
        validate_customer(c)
        self.assertIsNotNone(getattr(c, "default_price_list", None))

    def test_existing_price_list_not_overridden(self):
        from aimatic.offline_pos.customer_validation import validate_customer

        pl = frappe.db.get_value("Price List", {"selling": 1}, "name") or "Standard Selling"
        cg = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
        ter = frappe.db.get_value("Territory", {"is_group": 0}, "name") or "All Territories"
        c = frappe.new_doc("Customer")
        c.customer_name = "_CV PL Keep"
        c.customer_type = "Individual"
        c.customer_group = cg
        c.territory = ter
        c.default_price_list = pl
        validate_customer(c)
        self.assertEqual(c.default_price_list, pl)


# ---------------------------------------------------------------------------
# get_item_barcodes
# ---------------------------------------------------------------------------

class TestGetItemBarcodes(_AimTestCase):

    def test_guest_blocked(self):
        from aimatic.offline_pos.api import get_item_barcodes

        frappe.set_user("Guest")
        with self.assertRaises(FrappePermissionError):
            get_item_barcodes()

    def test_response_has_required_keys(self):
        from aimatic.offline_pos.api import get_item_barcodes

        result = get_item_barcodes(limit_start=0, limit_page_length=5)
        self.assertIn("rows", result)
        self.assertIn("has_more", result)
        self.assertIn("next_start", result)

    def test_page_size_capped_at_max(self):
        """Requesting > _MAX_PAGE_SIZE rows: the SQL LIMIT must be capped."""
        from aimatic.offline_pos.api import _MAX_PAGE_SIZE, get_item_barcodes

        captured = {}
        real_sql = frappe.db.sql

        def spy_sql(sql, params=None, **kw):
            if params and "limit" in (params or {}):
                captured["limit"] = params["limit"]
            return real_sql(sql, params, **kw) if params else real_sql(sql, **kw)

        with patch.object(frappe.db, "sql", side_effect=spy_sql):
            get_item_barcodes(limit_start=0, limit_page_length=9999)

        if "limit" in captured:
            self.assertLessEqual(captured["limit"], _MAX_PAGE_SIZE + 1)

    def test_inactive_item_excluded(self):
        """Disabled items and non-sales items must not appear in results."""
        # Create test items inline so we can assert precisely
        def _item(code, is_sales=1, disabled=0, barcode=None):
            if not frappe.db.exists("Item", code):
                ig = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
                doc = frappe.get_doc({
                    "doctype": "Item",
                    "item_code": code,
                    "item_name": code,
                    "item_group": ig,
                    "stock_uom": "Nos",
                    "is_sales_item": is_sales,
                    "disabled": disabled,
                })
                doc.insert(ignore_permissions=True)
            else:
                doc = frappe.get_doc("Item", code)
            if barcode and not frappe.db.exists("Item Barcode", {"barcode": barcode}):
                doc.append("barcodes", {"barcode": barcode, "uom": "Nos"})
                doc.save(ignore_permissions=True)

        _item("_BC Active AIM", barcode="BC-AIM-ACTIVE001")
        _item("_BC Inactive AIM", disabled=1, barcode="BC-AIM-INACT001")
        _item("_BC NonSales AIM", is_sales=0, barcode="BC-AIM-NSALE001")

        from aimatic.offline_pos.api import get_item_barcodes

        result = get_item_barcodes(limit_start=0, limit_page_length=1000)
        codes = {r["item_code"] for r in result["rows"]}

        self.assertIn("_BC Active AIM", codes)
        self.assertNotIn("_BC Inactive AIM", codes)
        self.assertNotIn("_BC NonSales AIM", codes)

    def test_has_more_and_next_start(self):
        """When a second page exists, has_more is True and next_start is set."""
        from aimatic.offline_pos.api import get_item_barcodes

        result = get_item_barcodes(limit_start=0, limit_page_length=1)
        if result["has_more"]:
            self.assertEqual(result["next_start"], 1)
            self.assertEqual(len(result["rows"]), 1)
        else:
            # Fewer than 2 active barcode items — skip remaining assertions
            self.assertIsNone(result["next_start"])

    def test_empty_barcode_rows_not_returned(self):
        from aimatic.offline_pos.api import get_item_barcodes

        result = get_item_barcodes(limit_start=0, limit_page_length=100)
        for row in result["rows"]:
            self.assertTrue(row.get("barcode"), "Empty barcode in result")

    def test_non_positive_page_length_defaults(self):
        from aimatic.offline_pos.api import get_item_barcodes

        result = get_item_barcodes(limit_start=0, limit_page_length=0)
        self.assertIn("rows", result)


# ---------------------------------------------------------------------------
# preview_cart — auth guard
# ---------------------------------------------------------------------------

class TestPreviewCartGuest(_AimTestCase):

    def test_guest_blocked(self):
        from aimatic.offline_pos.api import preview_cart

        frappe.set_user("Guest")
        with self.assertRaises(FrappePermissionError):
            preview_cart("any_profile", "any_customer", [])


# ---------------------------------------------------------------------------
# preview_cart — profile / session validation
# ---------------------------------------------------------------------------

class TestPreviewCartValidation(_AimTestCase):

    def test_invalid_pos_profile_throws(self):
        from aimatic.offline_pos.api import preview_cart

        with self.assertRaises(frappe.ValidationError):
            preview_cart("_NONEXISTENT_PROFILE_XYZ", "any_cust", [])

    @_require_fixtures
    def test_missing_opening_entry_throws(self):
        """Without a submitted POS Opening Entry, preview_cart must raise."""
        from aimatic.offline_pos.api import preview_cart

        # No opening entry created — _validate_pos_opening_entry raises before FBR
        with self.assertRaises(frappe.ValidationError):
            preview_cart(
                _POS_PROFILE_NAME,
                _CUSTOMER_NAME,
                [{"item_code": _ITEM_CODE, "qty": 1}],
            )

    @_require_fixtures
    def test_user_not_in_profile_is_blocked(self):
        """A user absent from applicable_for_users must be denied."""
        from aimatic.offline_pos.api import preview_cart

        # Use a real linked User because Frappe v16 validates Link rows when
        # inserting the cloned POS Profile.
        stranger, _password = _make_cashier(roles=("POS User",))
        restricted_name = _make_restricted_pos_profile(stranger)
        frappe.clear_document_cache("POS Profile", restricted_name)

        with self.assertRaises(FrappePermissionError):
            preview_cart(
                restricted_name,
                _CUSTOMER_NAME,
                [{"item_code": _ITEM_CODE, "qty": 1}],
            )

    def test_invalid_customer_throws(self):
        """An unknown customer name must raise ValidationError."""
        from aimatic.offline_pos.api import preview_cart

        if not _POS_PROFILE_NAME:
            raise unittest.SkipTest("No POS Profile on site")

        # Create the opening entry so we get past the session guard
        _create_opening_entry(_POS_PROFILE_NAME)

        # _load_customer raises before FBR is ever reached
        with self.assertRaises(frappe.ValidationError):
            preview_cart(
                _POS_PROFILE_NAME,
                "_NONEXISTENT_CUSTOMER_XYZ",
                [{"item_code": _ITEM_CODE or "X", "qty": 1}],
            )

    @_require_fixtures
    def test_preview_uses_human_cashier_opening_not_terminal_user(self):
        """Electron preview must resolve the shift by cashier_user."""
        from aimatic.offline_pos.api import preview_cart

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _POS_PROFILE_NAME
        _create_opening_entry(profile, user=cashier)

        with self.assertRaisesRegex(frappe.ValidationError, "Invalid Customer"):
            preview_cart(
                profile,
                "_NONEXISTENT_CUSTOMER_XYZ",
                [{"item_code": _ITEM_CODE or "X", "qty": 1}],
                cashier_user=cashier,
            )


# ---------------------------------------------------------------------------
# preview_cart — functional tests
# ---------------------------------------------------------------------------

class TestPreviewCartFunctional(_AimTestCase):
    """Tests that require a valid POS session (profile + opening entry)."""

    def setUp(self):
        super().setUp()
        if not (_POS_PROFILE_NAME and _ITEM_CODE and _CUSTOMER_NAME):
            raise unittest.SkipTest("Required fixtures missing from site")
        self.pos_name = _POS_PROFILE_NAME
        self.item_code = _ITEM_CODE
        self.customer_name = _CUSTOMER_NAME
        self.opening = _create_opening_entry(self.pos_name, "Administrator")

    # -- quantity guard -------------------------------------------------------

    def test_zero_quantity_throws(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            with self.assertRaises(frappe.ValidationError):
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": self.item_code, "qty": 0}],
                )

    def test_negative_quantity_throws(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            with self.assertRaises(frappe.ValidationError):
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": self.item_code, "qty": -1}],
                )

    def test_qty_checked_before_fbr(self):
        """FBR functions must not be called when qty is invalid."""
        from aimatic.offline_pos.api import preview_cart

        with patch("aimatic.fbr_pos.payload_builder.build_pos_payload") as mock_build:
            try:
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": self.item_code, "qty": 0}],
                )
            except frappe.ValidationError:
                pass
            mock_build.assert_not_called()

    # -- zero-rate guard -------------------------------------------------------

    def test_zero_rate_item_throws(self):
        """An item with no priced Item Price for this selling price list
        resolves to rate=0 through ERPNext's own pricing pipeline; this must
        be rejected rather than sold for free."""
        from aimatic.offline_pos.api import preview_cart

        code = "_AIM Zero Rate Test Item"
        if not frappe.db.exists("Item", code):
            ig = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
            frappe.get_doc({
                "doctype": "Item",
                "item_code": code,
                "item_name": code,
                "item_group": ig,
                "stock_uom": "Nos",
                "is_sales_item": 1,
            }).insert(ignore_permissions=True)
        # Deliberately no Item Price row for this item on any price list.

        with _patch_fbr():
            with self.assertRaises(frappe.ValidationError):
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": code, "qty": 1}],
                )

    # -- payload parsing ------------------------------------------------------

    def test_empty_items_returns_empty(self):
        from aimatic.offline_pos.api import preview_cart

        result = preview_cart(self.pos_name, self.customer_name, [])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["taxes"], [])

    def test_json_string_items_accepted(self):
        import json
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                json.dumps([{"item_code": self.item_code, "qty": 1}]),
            )
        self.assertEqual(len(result["rows"]), 1)

    def test_list_items_accepted(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 2}],
            )
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["quantity"], 2.0)

    def test_unknown_item_throws(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            with self.assertRaises(frappe.ValidationError):
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": "_ITEM_DOES_NOT_EXIST_XYZ", "qty": 1}],
                )

    # -- response structure ---------------------------------------------------

    def test_response_top_level_keys(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )
        for k in ("rows", "taxes", "totals"):
            self.assertIn(k, result)

    def test_totals_keys_present(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )
        for k in ("total", "net_total", "grand_total", "currency"):
            self.assertIn(k, result["totals"])

    def test_taxes_not_in_item_rows(self):
        """Per-item 'tax_rows' must not appear — taxes are top-level only."""
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )
        for row in result["rows"]:
            self.assertNotIn("tax_rows", row)

    def test_grand_total_positive(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )
        self.assertGreater(result["totals"]["grand_total"] or 0, 0)

    # -- no DB side-effects ---------------------------------------------------

    def test_no_pos_invoice_inserted(self):
        from aimatic.offline_pos.api import preview_cart

        count_before = frappe.db.count("POS Invoice", {"customer": self.customer_name})
        with _patch_fbr():
            preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )
        count_after = frappe.db.count("POS Invoice", {"customer": self.customer_name})
        self.assertEqual(count_before, count_after)

    def test_no_fbr_network_request(self):
        """requests.post must never be called during preview."""
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr():
            with patch("requests.post") as mock_post:
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": self.item_code, "qty": 1}],
                )
                mock_post.assert_not_called()

    # -- FBR pipeline ---------------------------------------------------------

    def test_fbr_build_and_accounting_are_called(self):
        from aimatic.offline_pos.api import preview_cart

        with patch(
            "aimatic.fbr_pos.payload_builder.build_pos_payload", return_value={}
        ) as mock_build, patch(
            "aimatic.fbr_pos.accounting.apply_fbr_accounting_rows", return_value=None
        ) as mock_acc:
            preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )
        mock_build.assert_called_once()
        mock_acc.assert_called_once()

    def test_fbr_tax_row_appears_in_top_level_taxes(self):
        """FBR tax rows appended by apply_fbr_accounting_rows are returned at top level."""
        from aimatic.offline_pos.api import preview_cart

        def _fake_accounting(doc):
            doc.append(
                "taxes",
                {
                    "charge_type": "Actual",
                    "account_head": frappe.db.get_value(
                        "Account",
                        {"account_type": "Tax", "company": doc.company, "is_group": 0},
                        "name",
                    ) or "Test Tax Account",
                    "description": "FBR Sales Tax",
                    "tax_amount": 170,
                    # ERPNext v16 rejects Actual charges marked inclusive.
                    "included_in_print_rate": 0,
                },
            )

        with patch("aimatic.fbr_pos.payload_builder.build_pos_payload", return_value={}), \
             patch("aimatic.fbr_pos.accounting.apply_fbr_accounting_rows",
                   side_effect=_fake_accounting):
            result = preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
            )

        descriptions = [t.get("description") for t in result["taxes"]]
        self.assertIn("FBR Sales Tax", descriptions)

    # -- coupon validation ----------------------------------------------------

    def test_coupon_error_propagates(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr(), patch(
            "erpnext.accounts.doctype.pricing_rule.utils.validate_coupon_code",
            side_effect=frappe.ValidationError("Coupon expired"),
        ):
            with self.assertRaises(frappe.ValidationError):
                preview_cart(
                    self.pos_name,
                    self.customer_name,
                    [{"item_code": self.item_code, "qty": 1}],
                    coupon_code="EXPIRED-COUPON",
                )

    def test_coupon_usage_count_not_updated(self):
        """update_coupon_code_count must never be called during preview."""
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr(), patch(
            "erpnext.accounts.doctype.pricing_rule.utils.validate_coupon_code"
        ), patch(
            "erpnext.accounts.doctype.pricing_rule.utils.update_coupon_code_count"
        ) as mock_update:
            preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
                coupon_code="ANY-COUPON",
            )
            mock_update.assert_not_called()

    # -- loyalty redemption ---------------------------------------------------

    def test_loyalty_validation_called_when_redeeming(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr(), patch(
            "erpnext.accounts.doctype.loyalty_program.loyalty_program.validate_loyalty_points",
            wraps=lambda *a, **kw: None,
        ) as mock_lp:
            preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
                redeem_loyalty_points=1,
                loyalty_points=10,
            )
            mock_lp.assert_called_once()

    def test_loyalty_not_called_when_not_redeeming(self):
        from aimatic.offline_pos.api import preview_cart

        with _patch_fbr(), patch(
            "erpnext.accounts.doctype.loyalty_program.loyalty_program.validate_loyalty_points"
        ) as mock_lp:
            preview_cart(
                self.pos_name,
                self.customer_name,
                [{"item_code": self.item_code, "qty": 1}],
                redeem_loyalty_points=0,
            )
            mock_lp.assert_not_called()


# ---------------------------------------------------------------------------
# get_customer_benefits
# ---------------------------------------------------------------------------

class TestGetCustomerBenefits(_AimTestCase):

    def setUp(self):
        super().setUp()
        if not (_POS_PROFILE_NAME and _CUSTOMER_NAME):
            raise unittest.SkipTest("Required fixtures missing from site")
        self.pos_name = _POS_PROFILE_NAME
        self.customer_name = _CUSTOMER_NAME

    def test_guest_blocked(self):
        from aimatic.offline_pos.api import get_customer_benefits

        frappe.set_user("Guest")
        with self.assertRaises(FrappePermissionError):
            get_customer_benefits(self.pos_name, self.customer_name)

    def test_customer_without_loyalty_returns_zero(self):
        from aimatic.offline_pos.api import get_customer_benefits

        cust = _make_test_customer("_GB No Loyalty")
        result = get_customer_benefits(self.pos_name, cust.name)

        self.assertIsNone(result["loyalty_program"])
        self.assertEqual(result["available_loyalty_points"], 0)
        self.assertEqual(result["loyalty_value"], 0)
        self.assertIn("expense_account", result)

    def test_invalid_customer_throws(self):
        from aimatic.offline_pos.api import get_customer_benefits

        with self.assertRaises(frappe.ValidationError):
            get_customer_benefits(self.pos_name, "_NONEXISTENT_CUST_XYZ_GB")

    def test_invalid_pos_profile_throws(self):
        from aimatic.offline_pos.api import get_customer_benefits

        with self.assertRaises(frappe.ValidationError):
            get_customer_benefits("_NONEXISTENT_PROFILE_GB", self.customer_name)


# ---------------------------------------------------------------------------
# Context manager: stub FBR build/accounting
# ---------------------------------------------------------------------------

class TestPosRefund(_AimTestCase):
    """Refund endpoint guard rails that run without FBR settings or network.

    Full end-to-end refund submission (create return, FBR credit note) requires
    the aimatic app installed on a site with FBR Integration Settings; those
    scenarios are documented in the task report and exercised manually.
    """

    def test_get_invoice_for_refund_guest_blocked(self):
        from aimatic.offline_pos.api import get_pos_invoice_for_refund

        frappe.set_user("Guest")
        with self.assertRaises(FrappePermissionError):
            get_pos_invoice_for_refund("ANY")

    def test_submit_refund_guest_blocked(self):
        from aimatic.offline_pos.api import submit_pos_refund

        frappe.set_user("Guest")
        with self.assertRaises(FrappePermissionError):
            submit_pos_refund("rid", "ANY", "cashier@example.com")

    def test_submit_refund_requires_terminal_refund_id(self):
        from aimatic.offline_pos.api import submit_pos_refund

        with self.assertRaises(frappe.ValidationError):
            submit_pos_refund(
                "", "ANY", "cashier@example.com",
                items=[{"original_row_name": "x", "qty": 1}],
            )

    def test_get_invoice_for_refund_requires_name(self):
        from aimatic.offline_pos.api import get_pos_invoice_for_refund

        with self.assertRaises(frappe.ValidationError):
            get_pos_invoice_for_refund("")

    def test_fbr_acceptance_requires_code_and_number(self):
        from aimatic.offline_pos.api import _normalize_refund_fbr_status

        # Accepted only with explicit Accepted status AND a real invoice number.
        self.assertEqual(_normalize_refund_fbr_status("Accepted", "FBR-123"), "Accepted")
        # HTTP-200-style success without a real number is never Accepted.
        for bad in ["", None, "N/A", "Not Available", "none", "null"]:
            self.assertEqual(_normalize_refund_fbr_status("Accepted", bad), "Pending")
        self.assertEqual(_normalize_refund_fbr_status("Failed", "FBR-1"), "Failed")
        self.assertEqual(_normalize_refund_fbr_status("Sending", "FBR-1"), "Pending")

    def test_refund_permission_allows_supervisor_role(self):
        from aimatic.offline_pos.api import _require_refund_permission

        pos = frappe._dict(name="POS-A")
        pos.meta = frappe._dict(has_field=lambda fieldname: False)

        with patch("aimatic.offline_pos.api.frappe.get_roles", return_value=["POS Supervisor"]):
            _require_refund_permission(pos)

    def test_refund_permission_allows_profile_user(self):
        from aimatic.offline_pos.api import _require_refund_permission

        frappe.set_user("cashier@example.com")
        pos = frappe._dict(
            name="POS-A",
            custom_refund_allowed_users=[frappe._dict(user="cashier@example.com")],
        )
        pos.meta = frappe._dict(
            has_field=lambda fieldname: fieldname == "custom_refund_allowed_users"
        )

        with patch("aimatic.offline_pos.api.frappe.get_roles", return_value=[]):
            _require_refund_permission(pos)

    def test_refund_permission_blocks_empty_profile_allow_list(self):
        from aimatic.offline_pos.api import _require_refund_permission

        frappe.set_user("cashier@example.com")
        pos = frappe._dict(name="POS-A", custom_refund_allowed_users=[])
        pos.meta = frappe._dict(
            has_field=lambda fieldname: fieldname == "custom_refund_allowed_users"
        )

        with patch("aimatic.offline_pos.api.frappe.get_roles", return_value=[]):
            with self.assertRaises(FrappePermissionError):
                _require_refund_permission(pos)

    def test_refund_permission_blocks_missing_role_without_profile_field(self):
        from aimatic.offline_pos.api import _require_refund_permission

        frappe.set_user("cashier@example.com")
        pos = frappe._dict(name="POS-A")
        pos.meta = frappe._dict(has_field=lambda fieldname: False)

        with patch("aimatic.offline_pos.api.frappe.get_roles", return_value=[]):
            with self.assertRaises(FrappePermissionError):
                _require_refund_permission(pos)

    def test_returned_qty_aggregates_by_original_row(self):
        from unittest.mock import patch as _patch
        from aimatic.offline_pos.api import _validate_refund_quantities

        class _Original:
            name = "ORIG-1"
            items = [frappe._dict(name="row-a", qty=5, item_code="ITEM-A")]

        original = _Original()
        # 2 already returned -> remaining 3; requesting 4 must fail, 3 must pass.
        with _patch(
            "aimatic.offline_pos.api.returned_qty_by_row", return_value={"row-a": 2}
        ):
            with self.assertRaises(frappe.ValidationError):
                _validate_refund_quantities(original, {"row-a": 4})
            _validate_refund_quantities(original, {"row-a": 3})  # exactly remaining: ok

    def test_preserve_original_return_row_values_prorates_amounts(self):
        from aimatic.offline_pos.api import _preserve_original_return_row_values

        row = frappe._dict()
        original = frappe._dict(
            name="row-a",
            item_code="ITEM-A",
            item_name="Item A",
            uom="Nos",
            stock_uom="Nos",
            conversion_factor=1,
            warehouse="Stores",
            qty=4,
            rate=50,
            price_list_rate=60,
            discount_percentage=5,
            discount_amount=10,
            net_rate=45,
            amount=200,
            net_amount=180,
        )

        _preserve_original_return_row_values(row, original, 1)

        self.assertEqual(row.item_code, "ITEM-A")
        self.assertEqual(row.rate, 50)
        self.assertEqual(row.amount, -50)
        self.assertEqual(row.net_amount, -45)

    def test_build_refund_response_includes_fbr_item_and_totals(self):
        from unittest.mock import patch as _patch
        from aimatic.offline_pos.api import _build_refund_response

        class _Meta:
            def has_field(self, fieldname):
                return True

        doc = frappe._dict(
            meta=_Meta(),
            name="RET-1",
            return_against="POS-1",
            posting_date="2026-06-26",
            posting_time="10:00:00",
            customer="CUST-1",
            grand_total=-117,
            rounded_total=-117,
            custom_fbr_status="Accepted",
            custom_fbr_invoice_number="FBR-RET-1",
            custom_fbr_http_status=200,
            custom_fbr_error="",
            items=[
                frappe._dict(
                    item_code="ITEM-A",
                    qty=-1,
                    rate=117,
                    net_rate=100,
                    amount=-117,
                    net_amount=-100,
                    custom_fbr_sales_tax=17,
                    custom_fbr_value_excluding_tax=100,
                    pos_invoice_item="row-a",
                )
            ],
            payments=[frappe._dict(mode_of_payment="Cash", amount=-117)],
        )
        original = frappe._dict(
            taxes=[
                frappe._dict(description="FBR POS Service Fee", tax_amount=1),
            ],
        )

        with _patch("aimatic.offline_pos.api.frappe.get_doc", return_value=original):
            response = _build_refund_response(doc)

        self.assertEqual(response["items"][0]["sales_tax"], 17)
        self.assertEqual(response["items"][0]["value_excluding_tax"], 100)
        self.assertEqual(response["refund_totals"]["merchandise_refund"], 117)
        self.assertEqual(response["refund_totals"]["gst_refund"], 17)
        self.assertEqual(response["refund_totals"]["non_refundable_fbr_pos_fee"], 1)
        self.assertEqual(response["refund_totals"]["total_refund"], 117)

    def test_build_return_item_payload_uses_original_snapshot(self):
        from unittest.mock import patch as _patch
        from aimatic.fbr_pos.payload_builder import build_return_item_payload

        class _Meta:
            def has_field(self, fieldname):
                return True

        row = frappe._dict(
            meta=_Meta(),
            item_code="ITEM-A",
            item_name="Item A",
            qty=-1,
            pos_invoice_item="row-a",
        )
        original = frappe._dict(
            meta=_Meta(),
            item_name="Item A",
            qty=4,
            discount_amount=8,
            custom_fbr_value_excluding_tax=400,
            custom_fbr_sales_tax=68,
            custom_fbr_retail_price=0,
            custom_fbr_tax_category="Standard",
            custom_fbr_sale_type="Goods at standard rate",
            custom_fbr_tax_rate=17,
            custom_fbr_is_third_schedule=0,
            custom_fbr_mrp=0,
            custom_fbr_hs_code="01011000",
        )

        with _patch(
            "aimatic.fbr_pos.payload_builder._get_original_return_row",
            return_value=original,
        ):
            payload = build_return_item_payload(row)

        self.assertEqual(payload["InvoiceType"], 1)
        self.assertEqual(payload["SaleValue"], 100)
        self.assertEqual(payload["TaxCharged"], 17)
        self.assertEqual(payload["TotalAmount"], 117)
        self.assertEqual(payload["Discount"], 2)
        self.assertEqual(row.custom_fbr_sales_tax, 17)


# ---------------------------------------------------------------------------
# Cashier-aware endpoints — pos_cashier_login, start_pos_session,
# get_active_pos_session, get_pos_closing_summary, close_pos_session,
# submit_online_sale
# ---------------------------------------------------------------------------

def _make_cashier(roles=("POS User",), enabled=1, password="Cashier@12345"):
    """Create a throw-away cashier User with the given roles.

    Rolled back with the rest of the test transaction. Each call uses a
    unique email, so a stale Redis role-cache entry from an earlier test can
    never be mistaken for this user's roles.
    """
    email = "cashier_{0}@example.com".format(frappe.generate_hash(length=8))
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": "Test Cashier",
        "send_welcome_email": 0,
        "enabled": enabled,
    })
    user.insert(ignore_permissions=True)
    if roles:
        user.add_roles(*roles)

    from frappe.utils.password import update_password
    update_password(email, password)

    return email, password


def _https_request():
    """Context manager satisfying _require_https_for_pos_admin_authorization -
    there is no real HTTP request in a console/test context, so
    frappe.local.request is normally absent (_is_https_request returns False).
    Patches only the .request attribute on the real frappe.local (not the
    whole object - frappe.local is a shared thread-local proxy other Frappe
    internals rely on for .site/.controllers/etc mid-call, e.g. frappe.get_doc
    inside close_pos_session; replacing it wholesale breaks those unrelated to
    the HTTPS check this is actually testing)."""
    return patch.object(
        frappe.local,
        "request",
        SimpleNamespace(scheme="https", headers={}),
        create=True,
    )


class TestPosAdminAuthorization(_AimTestCase):
    """authorize_pos_admin_action / consume_pos_admin_authorization - the
    supervisor step-up primitive shared by close_shift (offline_pos/api.py's
    close_pos_session) and void_item (Electron client only, no server
    document of its own)."""

    def test_authorize_success(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request():
            result = authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")

        self.assertIn("token", result)
        self.assertTrue(result["token"])
        self.assertIn("expires_at", result)
        auth = frappe.get_all(
            "POS Admin Authorization",
            filters={"user": supervisor, "action": "void_item", "terminal_id": "TEST-TERM-1"},
            fields=["used"],
        )
        self.assertEqual(len(auth), 1)
        self.assertEqual(auth[0].used, 0)
        log = frappe.get_all(
            "POS Admin Audit Log",
            filters={"user": supervisor, "action": "void_item", "status": "success"},
        )
        self.assertEqual(len(log), 1)

    def test_authorize_wrong_password(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action

        supervisor, _password = _make_cashier(roles=("POS Supervisor",))
        with _https_request(), self.assertRaises(frappe.AuthenticationError):
            authorize_pos_admin_action(supervisor, "definitely-wrong", "void_item", "TEST-TERM-1")

    def test_authorize_disabled_user(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action

        supervisor, password = _make_cashier(roles=("POS Supervisor",), enabled=0)
        with _https_request(), self.assertRaises(frappe.AuthenticationError):
            authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")

    def test_authorize_missing_role(self):
        """A plain POS User cannot authorize - only POS Supervisor/System Manager can."""
        from aimatic.offline_pos.api import authorize_pos_admin_action

        cashier, password = _make_cashier(roles=("POS User",))
        with _https_request(), self.assertRaises(FrappePermissionError):
            authorize_pos_admin_action(cashier, password, "void_item", "TEST-TERM-1")

    def test_authorize_invalid_action(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request(), self.assertRaises(frappe.ValidationError):
            authorize_pos_admin_action(supervisor, password, "not_a_real_action", "TEST-TERM-1")

    def test_authorize_requires_https(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with self.assertRaises(frappe.ValidationError):
            authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")

    def test_consume_success(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action, consume_pos_admin_authorization

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request():
            minted = authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")
            result = consume_pos_admin_authorization(minted["token"], "void_item", "TEST-TERM-1")

        self.assertTrue(result["success"])
        used = frappe.get_all(
            "POS Admin Authorization",
            filters={"user": supervisor, "action": "void_item", "terminal_id": "TEST-TERM-1"},
            fields=["used"],
        )[0]
        self.assertEqual(used.used, 1)

    def test_consume_invalid_token(self):
        from aimatic.offline_pos.api import consume_pos_admin_authorization

        with _https_request(), self.assertRaises(FrappePermissionError):
            consume_pos_admin_authorization("not-a-real-token", "void_item", "TEST-TERM-1")

    def test_consume_wrong_action_blocked(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action, consume_pos_admin_authorization

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request():
            minted = authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")
            with self.assertRaises(FrappePermissionError):
                consume_pos_admin_authorization(minted["token"], "close_shift", "TEST-TERM-1")

    def test_consume_wrong_terminal_blocked(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action, consume_pos_admin_authorization

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request():
            minted = authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")
            with self.assertRaises(FrappePermissionError):
                consume_pos_admin_authorization(minted["token"], "void_item", "OTHER-TERMINAL")

    def test_consume_reused_token_blocked(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action, consume_pos_admin_authorization

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request():
            minted = authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")
            consume_pos_admin_authorization(minted["token"], "void_item", "TEST-TERM-1")
            with self.assertRaises(FrappePermissionError):
                consume_pos_admin_authorization(minted["token"], "void_item", "TEST-TERM-1")

    def test_consume_expired_token_blocked(self):
        from frappe.utils import add_to_date, now_datetime

        from aimatic.offline_pos.api import authorize_pos_admin_action, consume_pos_admin_authorization

        supervisor, password = _make_cashier(roles=("POS Supervisor",))
        with _https_request():
            minted = authorize_pos_admin_action(supervisor, password, "void_item", "TEST-TERM-1")
            auth_name = frappe.db.get_value(
                "POS Admin Authorization",
                {"user": supervisor, "action": "void_item", "terminal_id": "TEST-TERM-1"},
                "name",
            )
            frappe.db.set_value(
                "POS Admin Authorization", auth_name, "expires_at", add_to_date(now_datetime(), minutes=-1)
            )
            with self.assertRaises(FrappePermissionError):
                consume_pos_admin_authorization(minted["token"], "void_item", "TEST-TERM-1")


class TestPosCashierLogin(_AimTestCase):
    """pos_cashier_login: credential/role/profile checks, no session created."""

    @_require_fixtures
    def test_login_success(self):
        from aimatic.offline_pos.api import pos_cashier_login

        cashier, password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)

        result = pos_cashier_login(cashier, password, "TERM-1", profile)

        self.assertTrue(result["success"])
        self.assertEqual(result["user"], cashier)
        self.assertEqual(result["allowed_pos_profiles"], [profile])
        self.assertEqual(result["default_pos_profile"], profile)
        self.assertTrue(result["can_start_shift"])
        self.assertTrue(result["require_pin_setup"])
        self.assertIn("offline_login_expires_at", result)
        # Caller's own session must be untouched — no cashier session created.
        self.assertEqual(frappe.session.user, "Administrator")

        log = frappe.get_all(
            "POS Cashier Login Log",
            filters={"user": cashier, "status": "success"},
            fields=["name"],
        )
        self.assertEqual(len(log), 1)

    @_require_fixtures
    def test_login_wrong_password(self):
        from aimatic.offline_pos.api import pos_cashier_login

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)

        with self.assertRaises(frappe.AuthenticationError):
            pos_cashier_login(cashier, "definitely-wrong", "TERM-1", profile)

    @_require_fixtures
    def test_login_disabled_cashier(self):
        from aimatic.offline_pos.api import pos_cashier_login

        cashier, password = _make_cashier(roles=("POS User",), enabled=0)
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)

        with self.assertRaises(frappe.AuthenticationError):
            pos_cashier_login(cashier, password, "TERM-1", profile)

    @_require_fixtures
    def test_login_missing_pos_role(self):
        from aimatic.offline_pos.api import pos_cashier_login

        cashier, password = _make_cashier(roles=())
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)

        with self.assertRaises(FrappePermissionError):
            pos_cashier_login(cashier, password, "TERM-1", profile)

    @_require_fixtures
    def test_login_pos_profile_not_allowed(self):
        from aimatic.offline_pos.api import pos_cashier_login

        cashier, password = _make_cashier(roles=("POS User",))
        other_cashier, _pw = _make_cashier(roles=("POS User",))
        # Profile is restricted to a different user entirely.
        profile = _make_restricted_pos_profile(other_cashier)
        frappe.clear_document_cache("POS Profile", profile)

        with self.assertRaises(FrappePermissionError):
            pos_cashier_login(cashier, password, "TERM-1", profile)


class TestStartPosSessionCashier(_AimTestCase):
    @_require_fixtures
    def test_start_shift_as_cashier(self):
        from aimatic.offline_pos.api import start_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)

        result = start_pos_session(profile, cashier)

        self.assertEqual(result["user"], cashier)
        self.assertEqual(result["cashier_user"], cashier)
        self.assertIn("cashier_full_name", result)
        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", result["name"], "user"), cashier
        )


class TestGetActivePosSessionCashier(_AimTestCase):
    @_require_fixtures
    def test_get_active_session_as_cashier(self):
        from aimatic.offline_pos.api import get_active_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        result = get_active_pos_session(profile, cashier)

        self.assertEqual(result["opening_entry"], opening.name)
        self.assertEqual(result["cashier_user"], cashier)
        self.assertEqual(result["pos_profile"], profile)
        self.assertEqual(result["status"], "Open")


class TestGetPosClosingSummaryCashier(_AimTestCase):
    @_require_fixtures
    def test_close_summary_as_cashier(self):
        from aimatic.offline_pos.api import get_pos_closing_summary

        cashier, _password = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        result = get_pos_closing_summary(opening.name, cashier)

        self.assertEqual(result["cashierUser"], cashier)
        self.assertEqual(result["openingEntry"], opening.name)

    @_require_fixtures
    def test_close_summary_wrong_cashier_blocked(self):
        from aimatic.offline_pos.api import get_pos_closing_summary

        owner_cashier, _pw1 = _make_cashier(roles=("POS Supervisor",))
        other_cashier, _pw2 = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(owner_cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=owner_cashier)

        with self.assertRaises(FrappePermissionError):
            get_pos_closing_summary(opening.name, other_cashier)


class TestClosePosSessionCashier(_AimTestCase):
    @_require_fixtures
    def test_close_shift_supervisor_allowed(self):
        from aimatic.offline_pos.api import close_pos_session

        cashier, _password = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        result = close_pos_session(opening.name, cashier, [])

        self.assertEqual(result["status"], "Closed")
        self.assertEqual(result["cashier_user"], cashier)
        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", opening.name, "status"), "Closed"
        )

    @_require_fixtures
    def test_supervisor_cashier_can_close_through_plain_pos_user_terminal(self):
        """The terminal session is a transport identity; the human cashier's
        POS Supervisor role is the close-shift authority."""
        from aimatic.offline_pos.api import close_pos_session

        cashier, _password = _make_cashier(roles=("POS Supervisor",))
        terminal_user, _terminal_password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        original_user = frappe.session.user
        try:
            frappe.set_user(terminal_user)
            self.assertFalse(
                frappe.has_permission("POS Closing Entry", ptype="create")
            )
            result = close_pos_session(opening.name, cashier, [])
        finally:
            frappe.set_user(original_user)

        self.assertEqual(result["status"], "Closed")
        self.assertEqual(result["cashier_user"], cashier)

    @_require_fixtures
    def test_plain_terminal_can_consolidate_shift_invoice_after_authorized_close(self):
        """Closing permissions must cover ERPNext's nested Sales Invoice too,
        not only the POS Closing Entry wrapper."""
        from aimatic.offline_pos.api import close_pos_session, submit_online_sale

        if not _STOCKED_ITEM_CODE:
            raise unittest.SkipTest("No item with on-hand stock found on site")

        cashier, _password = _make_cashier(roles=("POS Supervisor",))
        terminal_user, _terminal_password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _patch_fbr(), patch(
            "aimatic.fbr_pos.events.submit_pos_invoice_to_fbr", return_value=None
        ):
            sale = submit_online_sale(
                terminal_invoice_id="TI-CLOSE-{0}".format(frappe.generate_hash(length=6)),
                terminal_id="TERM-1",
                pos_profile=profile,
                opening_entry=opening.name,
                customer=_CUSTOMER_NAME,
                items=[{"item_code": _STOCKED_ITEM_CODE, "qty": 1}],
                payments=[{"mode_of_payment": "Cash", "amount": 999999}],
                cashier_user=cashier,
            )

        original_user = frappe.session.user
        try:
            frappe.set_user(terminal_user)
            self.assertFalse(frappe.has_permission("Sales Invoice", ptype="create"))
            result = close_pos_session(opening.name, cashier, [])
            self.assertEqual(frappe.session.user, terminal_user)
        finally:
            frappe.set_user(original_user)

        merge_log = frappe.db.get_value(
            "POS Invoice Merge Log",
            {"pos_closing_entry": result["closing_entry"]},
            "consolidated_invoice",
        )
        self.assertTrue(merge_log)
        self.assertEqual(
            frappe.db.get_value("POS Invoice", sale["pos_invoice"], "status"),
            "Consolidated",
        )

    @_require_fixtures
    def test_close_shift_normal_cashier_blocked(self):
        """A plain POS User can open a shift but cannot close it here."""
        from aimatic.offline_pos.api import close_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with self.assertRaises(FrappePermissionError):
            close_pos_session(opening.name, cashier, [])

        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", opening.name, "status"), "Open"
        )

    @_require_fixtures
    def test_close_shift_wrong_cashier_blocked(self):
        """Cannot close another cashier's shift, even with a supervisor role."""
        from aimatic.offline_pos.api import close_pos_session

        owner_cashier, _pw1 = _make_cashier(roles=("POS User",))
        other_supervisor, _pw2 = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(owner_cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=owner_cashier)

        with self.assertRaises(FrappePermissionError):
            close_pos_session(opening.name, other_supervisor, [])

        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", opening.name, "status"), "Open"
        )

    @_require_fixtures
    def test_close_shift_pos_user_with_valid_supervisor_token_allowed(self):
        """The core new behavior: a plain POS User's own shift closes once a
        separate supervisor has authorized it - the cashier never needs to
        hold the close-shift role themselves."""
        from aimatic.offline_pos.api import authorize_pos_admin_action, close_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        supervisor, supervisor_password = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _https_request():
            minted = authorize_pos_admin_action(
                supervisor, supervisor_password, "close_shift", "TEST-TERM-1"
            )
            result = close_pos_session(opening.name, cashier, [], supervisor_token=minted["token"])

        self.assertEqual(result["status"], "Closed")
        self.assertEqual(result["cashier_user"], cashier)
        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", opening.name, "status"), "Closed"
        )
        # Token is single-use - the same shift/token pair can't be replayed.
        used = frappe.db.get_value(
            "POS Admin Authorization",
            {"user": supervisor, "action": "close_shift", "terminal_id": "TEST-TERM-1"},
            "used",
        )
        self.assertEqual(used, 1)

    @_require_fixtures
    def test_close_shift_token_does_not_require_terminal_user_closing_docperm(self):
        """The fixed terminal API identity may be a plain POS User.  A valid
        supervisor token authorizes the human cashier's close without also
        granting the terminal identity broad POS Closing Entry DocPerm."""
        from aimatic.offline_pos.api import authorize_pos_admin_action, close_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        terminal_user, _terminal_password = _make_cashier(roles=("POS User",))
        supervisor, supervisor_password = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _https_request():
            minted = authorize_pos_admin_action(
                supervisor, supervisor_password, "close_shift", "TEST-TERM-1"
            )
            original_user = frappe.session.user
            try:
                frappe.set_user(terminal_user)
                self.assertFalse(
                    frappe.has_permission("POS Closing Entry", ptype="create")
                )
                result = close_pos_session(
                    opening.name, cashier, [], supervisor_token=minted["token"]
                )
            finally:
                frappe.set_user(original_user)

        self.assertEqual(result["status"], "Closed")
        self.assertEqual(result["cashier_user"], cashier)

    @_require_fixtures
    def test_close_shift_pos_user_invalid_token_blocked(self):
        from aimatic.offline_pos.api import close_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _https_request(), self.assertRaises(FrappePermissionError):
            close_pos_session(opening.name, cashier, [], supervisor_token="not-a-real-token")

        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", opening.name, "status"), "Open"
        )

    @_require_fixtures
    def test_close_shift_pos_user_reused_token_blocked(self):
        """A token already spent on one close (or any other consumption)
        cannot be replayed to close a second shift."""
        from aimatic.offline_pos.api import authorize_pos_admin_action, close_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        supervisor, supervisor_password = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _https_request():
            minted = authorize_pos_admin_action(
                supervisor, supervisor_password, "close_shift", "TEST-TERM-1"
            )
            close_pos_session(opening.name, cashier, [], supervisor_token=minted["token"])

            opening2 = _create_opening_entry(profile, user=cashier)
            with self.assertRaises(FrappePermissionError):
                close_pos_session(opening2.name, cashier, [], supervisor_token=minted["token"])

    @_require_fixtures
    def test_close_shift_pos_user_wrong_terminal_token_blocked(self):
        from aimatic.offline_pos.api import authorize_pos_admin_action, close_pos_session

        cashier, _password = _make_cashier(roles=("POS User",))
        supervisor, supervisor_password = _make_cashier(roles=("POS Supervisor",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _https_request():
            minted = authorize_pos_admin_action(
                supervisor, supervisor_password, "close_shift", "OTHER-TERMINAL"
            )
            with self.assertRaises(FrappePermissionError):
                close_pos_session(opening.name, cashier, [], supervisor_token=minted["token"])

        self.assertEqual(
            frappe.db.get_value("POS Opening Entry", opening.name, "status"), "Open"
        )


class TestSubmitOnlineSaleCashier(_AimTestCase):
    """Cashier-aware guard rails on submit_online_sale.

    Full end-to-end FBR submission is exercised in test_offline_sale_cashier_pin_accepted;
    the rejection-path tests below never reach invoice building or FBR at all.
    """

    @_require_fixtures
    def test_submit_sale_opening_entry_cashier_mismatch_blocked(self):
        from aimatic.offline_pos.api import submit_online_sale

        owner_cashier, _pw1 = _make_cashier(roles=("POS User",))
        other_cashier, _pw2 = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(owner_cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=owner_cashier)

        with self.assertRaises(FrappePermissionError):
            submit_online_sale(
                terminal_invoice_id="TI-MISMATCH-{0}".format(frappe.generate_hash(length=6)),
                terminal_id="TERM-1",
                pos_profile=profile,
                opening_entry=opening.name,
                customer=self._customer_or_skip(),
                items=[{"item_code": self._item_or_skip(), "qty": 1}],
                payments=[{"mode_of_payment": "Cash", "amount": 1}],
                cashier_user=other_cashier,
            )

    @_require_fixtures
    def test_offline_sale_cached_password_rejected(self):
        from aimatic.offline_pos.api import submit_online_sale

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with self.assertRaises(FrappePermissionError):
            submit_online_sale(
                terminal_invoice_id="TI-CACHEDPW-{0}".format(frappe.generate_hash(length=6)),
                terminal_id="TERM-1",
                pos_profile=profile,
                opening_entry=opening.name,
                customer=self._customer_or_skip(),
                items=[{"item_code": self._item_or_skip(), "qty": 1}],
                payments=[{"mode_of_payment": "Cash", "amount": 1}],
                cashier_user=cashier,
                offline_authenticated=1,
                offline_auth_method="cached_password",
            )

    @_require_fixtures
    def test_disabled_cashier_queued_sale_rejected(self):
        from aimatic.offline_pos.api import submit_online_sale

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        # Disabled after the shift was opened, e.g. mid-shift by an admin.
        frappe.db.set_value("User", cashier, "enabled", 0)

        with self.assertRaises(FrappePermissionError):
            submit_online_sale(
                terminal_invoice_id="TI-DISABLED-{0}".format(frappe.generate_hash(length=6)),
                terminal_id="TERM-1",
                pos_profile=profile,
                opening_entry=opening.name,
                customer=self._customer_or_skip(),
                items=[{"item_code": self._item_or_skip(), "qty": 1}],
                payments=[{"mode_of_payment": "Cash", "amount": 1}],
                cashier_user=cashier,
                offline_authenticated=1,
                offline_auth_method="cashier_pin",
            )

    @_require_fixtures
    def test_idempotent_terminal_invoice_id_still_works(self):
        from aimatic.offline_pos.api import submit_online_sale, _build_pos_invoice_doc

        if not _STOCKED_ITEM_CODE:
            raise unittest.SkipTest("No item with on-hand stock found on site")

        terminal_invoice_id = "TI-IDEMPOTENT-{0}".format(frappe.generate_hash(length=6))

        pos = frappe.get_cached_doc("POS Profile", _POS_PROFILE_NAME)
        cust = frappe.get_cached_doc("Customer", _CUSTOMER_NAME)
        if not frappe.db.exists("POS Opening Entry", {"pos_profile": pos.name, "user": "Administrator", "status": "Open", "docstatus": 1}):
            _create_opening_entry(pos.name, user="Administrator")
        with _patch_fbr():
            doc = _build_pos_invoice_doc(pos, cust, [{"item_code": _STOCKED_ITEM_CODE, "qty": 1}])
            doc.custom_terminal_invoice_id = terminal_invoice_id
            doc.append("payments", {"mode_of_payment": "Cash", "amount": doc.grand_total or 1})
            doc.insert(ignore_permissions=True)  # draft is enough: idempotency doesn't check docstatus

        before_count = frappe.db.count(
            "POS Invoice", {"custom_terminal_invoice_id": terminal_invoice_id}
        )
        self.assertEqual(before_count, 1)

        result = submit_online_sale(
            terminal_invoice_id=terminal_invoice_id,
            terminal_id="TERM-1",
            pos_profile=_POS_PROFILE_NAME,
            opening_entry="NONEXISTENT-DOES-NOT-MATTER",
            customer=_CUSTOMER_NAME,
            items=[{"item_code": _STOCKED_ITEM_CODE, "qty": 1}],
            payments=[{"mode_of_payment": "Cash", "amount": 1}],
            cashier_user="whoever@example.com",
        )

        self.assertEqual(result["pos_invoice"], doc.name)
        after_count = frappe.db.count(
            "POS Invoice", {"custom_terminal_invoice_id": terminal_invoice_id}
        )
        self.assertEqual(after_count, 1)

    @_require_fixtures
    def test_offline_sale_cashier_pin_accepted(self):
        from aimatic.offline_pos.api import submit_online_sale

        if not _STOCKED_ITEM_CODE:
            raise unittest.SkipTest("No item with on-hand stock found on site")

        cashier, _password = _make_cashier(roles=("POS User",))
        profile = _make_restricted_pos_profile(cashier)
        frappe.clear_document_cache("POS Profile", profile)
        opening = _create_opening_entry(profile, user=cashier)

        with _patch_fbr(), patch(
            "aimatic.fbr_pos.events.submit_pos_invoice_to_fbr", return_value=None
        ):
            result = submit_online_sale(
                terminal_invoice_id="TI-PIN-{0}".format(frappe.generate_hash(length=6)),
                terminal_id="TERM-1",
                pos_profile=profile,
                opening_entry=opening.name,
                customer=self._customer_or_skip(),
                items=[{"item_code": _STOCKED_ITEM_CODE, "qty": 1}],
                payments=[{"mode_of_payment": "Cash", "amount": 999999}],
                cashier_user=cashier,
                cashier_full_name="Test Cashier",
                offline_authenticated=1,
                offline_auth_method="cashier_pin",
                local_offline_session_id="offline-sess-1",
            )

        self.assertEqual(result["docstatus"], 1)
        self.assertEqual(result["cashier_user"], cashier)
        self.assertTrue(result["offline_authenticated"])
        # ERPNext's POS Closing Entry matches shift invoices by `owner`, so this
        # must be reattributed from the terminal's session to the cashier.
        self.assertEqual(
            frappe.db.get_value("POS Invoice", result["pos_invoice"], "owner"), cashier
        )
        self.assertEqual(
            frappe.db.get_value("POS Invoice", result["pos_invoice"], "custom_offline_auth_method"),
            "cashier_pin",
        )

    def _customer_or_skip(self):
        if not _CUSTOMER_NAME:
            raise unittest.SkipTest("No customer fixture on site")
        return _CUSTOMER_NAME

    def _item_or_skip(self):
        if not _ITEM_CODE:
            raise unittest.SkipTest("No item fixture on site")
        return _ITEM_CODE


class TestSubmitPosRefundCashier(_AimTestCase):
    """Cashier-aware guard rails on submit_pos_refund.

    Full end-to-end refund submission (original sale + FBR credit note) is
    out of scope here for the same reason as the rest of TestPosRefund: it
    requires FBR Integration Settings and network access. These tests target
    the cashier-identity/ownership hardening directly instead.
    """

    def test_submit_refund_requires_cashier_user(self):
        from aimatic.offline_pos.api import submit_pos_refund

        with self.assertRaises(FrappePermissionError):
            submit_pos_refund(
                "rid", "ANY", "",
                items=[{"original_row_name": "x", "qty": 1}],
            )

    def test_refund_permission_checks_passed_user_not_session(self):
        """_require_refund_permission(pos, user) must authorize `user`'s roles,
        not frappe.session.user — the terminal's own session is never the
        cashier processing the refund."""
        from aimatic.offline_pos.api import _require_refund_permission

        frappe.set_user("Administrator")  # session user would pass any role check
        pos = frappe._dict(name="POS-A")
        pos.meta = frappe._dict(has_field=lambda fieldname: False)

        def _roles_for(user):
            return ["POS Supervisor"] if user == "cashier@example.com" else []

        with patch("aimatic.offline_pos.api.frappe.get_roles", side_effect=_roles_for):
            # Explicit cashier with the role succeeds...
            _require_refund_permission(pos, "cashier@example.com")
            # ...but a different explicit user without the role is blocked,
            # even though frappe.session.user (Administrator) would pass.
            with self.assertRaises(FrappePermissionError):
                _require_refund_permission(pos, "someone_else@example.com")


class TestCashierMasterDataPermissions(_AimTestCase):
    """Regression test for the 2026-07-19 permission gap: the Electron client's
    Settings screen reads several master-data doctypes directly over Frappe's
    generic /api/resource REST endpoint (not through this module's whitelisted
    methods), so each read is subject to that doctype's own core DocPerm. Core
    ERPNext grants POS User/POS Supervisor nothing on any of these doctypes -
    fixed via Custom DocPerm fixtures (aimatic/fixtures/custom_docperm.json).
    This catches a future regression (fixture drift, an ERPNext upgrade
    resetting Custom DocPerm) automatically instead of only surfacing when a
    real cashier fails to load their POS Profile.
    """

    _READ_ONLY_DOCTYPES = [
        "POS Profile", "Company", "Sales Taxes and Charges Template",
        "Mode of Payment", "Coupon Code", "Customer Group", "Territory",
        "Item", "Item Price", "Bin", "Branch", "Print Format",
    ]

    def test_pos_user_and_supervisor_can_read_terminal_master_data(self):
        email, _ = _make_cashier(roles=("POS User", "POS Supervisor"))
        for doctype in self._READ_ONLY_DOCTYPES:
            with self.subTest(doctype=doctype):
                self.assertTrue(
                    frappe.has_permission(doctype, ptype="read", user=email),
                    f"POS User/POS Supervisor lost read access to {doctype}",
                )

    def test_pos_user_and_supervisor_can_read_and_create_customer(self):
        email, _ = _make_cashier(roles=("POS User", "POS Supervisor"))
        self.assertTrue(frappe.has_permission("Customer", ptype="read", user=email))
        self.assertTrue(frappe.has_permission("Customer", ptype="create", user=email))

    def test_pos_user_alone_has_the_same_master_data_access(self):
        """POS Supervisor is a superset in practice, but the Custom DocPerm
        grants are separate rows per role - verify POS User alone (no
        Supervisor) also passes, so an edit that only touches one of the two
        roles' rows is still caught."""
        email, _ = _make_cashier(roles=("POS User",))
        for doctype in self._READ_ONLY_DOCTYPES + ["Customer"]:
            with self.subTest(doctype=doctype):
                self.assertTrue(frappe.has_permission(doctype, ptype="read", user=email))


class _patch_fbr:
    """Stubs build_pos_payload and apply_fbr_accounting_rows so tests do not
    need FBR Integration Settings configured in the database.

    These are imported inside preview_cart, so we patch at the source module.
    """

    def __enter__(self):
        self._p1 = patch(
            "aimatic.fbr_pos.payload_builder.build_pos_payload", return_value={}
        )
        self._p2 = patch(
            "aimatic.fbr_pos.accounting.apply_fbr_accounting_rows", return_value=None
        )
        self._p1.start()
        self._p2.start()
        return self

    def __exit__(self, *args):
        self._p1.stop()
        self._p2.stop()
