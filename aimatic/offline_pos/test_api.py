"""
Tests for aimatic.offline_pos.api and aimatic.offline_pos.customer_validation.

Run with:
    bench --site <site> run-tests --app aimatic --module aimatic.offline_pos.test_api
"""
import unittest
from unittest.mock import patch

import frappe
from frappe.exceptions import PermissionError as FrappePermissionError

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


_POS_PROFILE_NAME = _site_pos_profile_name()
_ITEM_CODE = _site_item_code()
_CUSTOMER_NAME = _site_customer_name()


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

class _AimTestCase(unittest.TestCase):
    """Saves/restores the Frappe session user and rolls back the DB after each test."""

    def setUp(self):
        self._original_user = frappe.session.user
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user(self._original_user)
        frappe.db.rollback()


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
        "territory": ref.territory,
        "customer_group": ref.customer_group,
        "cost_center": ref.cost_center,
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

        # Create a profile that only allows 'some_stranger@example.com'
        restricted_name = _make_restricted_pos_profile("some_stranger@example.com")
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
                    "included_in_print_rate": 1,
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
            submit_pos_refund("rid", "ANY")

    def test_submit_refund_requires_terminal_refund_id(self):
        from aimatic.offline_pos.api import submit_pos_refund

        with self.assertRaises(frappe.ValidationError):
            submit_pos_refund("", "ANY", items=[{"original_row_name": "x", "qty": 1}])

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

    def test_returned_qty_aggregates_by_original_row(self):
        from unittest.mock import patch as _patch
        from aimatic.offline_pos.api import _validate_refund_quantities

        original = frappe._dict(
            name="ORIG-1",
            items=[frappe._dict(name="row-a", qty=5, item_code="ITEM-A")],
        )
        # 2 already returned -> remaining 3; requesting 4 must fail, 3 must pass.
        with _patch(
            "aimatic.offline_pos.api._returned_qty_by_row", return_value={"row-a": 2}
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

        self.assertEqual(payload["InvoiceType"], 2)
        self.assertEqual(payload["SaleValue"], 100)
        self.assertEqual(payload["TaxCharged"], 17)
        self.assertEqual(payload["TotalAmount"], 117)
        self.assertEqual(payload["Discount"], 2)
        self.assertEqual(row.custom_fbr_sales_tax, 17)


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
