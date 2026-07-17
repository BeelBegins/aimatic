"""
Tests for aimatic.purchase_history_autofill.

Run with:
    bench --site <site> run-tests --app aimatic --module aimatic.purchase_history_autofill.test_purchase_history_autofill

Covers the scenarios from the feature spec:
  - item exists in the latest purchase for the same supplier+branch
  - item exists only in an older purchase for the same supplier+branch
  - item exists for the same supplier but a different branch (must not match)
  - item exists for the same branch but a different supplier (must not match)
  - item never purchased for that supplier+branch (fields left empty)
  - multiple items with different histories in one document
  - Quantity and Rate are never touched
  - existing non-empty values are never overwritten
  - cancelled and return transactions are ignored
  - Purchase Receipt totals/calculations are unaffected
  - the field set is schema-driven (works if custom fields differ per site)
  - the hook fires through both "Get Items From Purchase Order" and manual
    item entry
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import frappe
from frappe.utils import add_days, nowdate

from aimatic.purchase_history_autofill.events import autofill_purchase_receipt_item_fields
from aimatic.purchase_history_autofill.utils import (
    _ALWAYS_EXCLUDE,
    apply_history_to_row,
    fetch_latest_history_rows,
    get_autofillable_fields,
)

SUPPLIER_A = "TEST-PHA-SUPPLIER-A"
SUPPLIER_B = "TEST-PHA-SUPPLIER-B"
ITEM_A = "TEST-PHA-ITEM-A"
ITEM_B = "TEST-PHA-ITEM-B"


def _first_branch_with_full_setup():
    """A real Branch on this site with company/warehouse/cost_center all
    set - reused rather than fabricated, since a valid Branch depends on a
    Company/Warehouse/Cost Center chain that's expensive to construct from
    scratch and differs per site (rule 14)."""
    return frappe.db.get_value(
        "Branch",
        {"finished_goods_warehouse": ["is", "set"], "cost_center": ["is", "set"], "company": ["is", "set"]},
        ["name", "company", "finished_goods_warehouse", "cost_center"],
        as_dict=True,
    )


def _second_branch(exclude_name):
    return frappe.db.get_value(
        "Branch",
        {
            "name": ["!=", exclude_name],
            "finished_goods_warehouse": ["is", "set"],
            "cost_center": ["is", "set"],
            "company": ["is", "set"],
        },
        ["name", "company", "finished_goods_warehouse", "cost_center"],
        as_dict=True,
    )


_BRANCH = _first_branch_with_full_setup()
_OTHER_BRANCH = _second_branch(_BRANCH.name) if _BRANCH else None


def _require_branch(test):
    def wrapper(self):
        if not _BRANCH:
            raise unittest.SkipTest("No fully-configured Branch found on this site")
        test(self)
    wrapper.__name__ = test.__name__
    return wrapper


class _PHATestCase(unittest.TestCase):
    """Mirrors offline_pos/test_api.py's _AimTestCase: rolls back the DB
    after every test, so per-test fixture creation is cheap and each test
    is fully isolated (no setUpClass-level shared state to worry about)."""

    def setUp(self):
        self._original_user = frappe.session.user
        frappe.set_user("Administrator")
        if _BRANCH:
            frappe.db.set_single_value("Buying Settings", "po_required", "No")
            frappe.db.set_single_value("Buying Settings", "pr_required", "No")
            self._make_fixtures()

    def tearDown(self):
        frappe.set_user(self._original_user)
        frappe.db.rollback()

    def _make_fixtures(self):
        for supplier in (SUPPLIER_A, SUPPLIER_B):
            if not frappe.db.exists("Supplier", supplier):
                supplier_group = frappe.db.get_value("Supplier Group", {}, "name")
                frappe.get_doc({
                    "doctype": "Supplier",
                    "supplier_name": supplier,
                    "supplier_group": supplier_group,
                }).insert(ignore_permissions=True)

        for item_code in (ITEM_A, ITEM_B):
            if not frappe.db.exists("Item", item_code):
                item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
                stock_uom = frappe.db.get_value("UOM", {}, "name") or "Nos"
                frappe.get_doc({
                    "doctype": "Item",
                    "item_code": item_code,
                    "item_name": item_code,
                    "item_group": item_group,
                    "stock_uom": stock_uom,
                    "is_stock_item": 1,
                }).insert(ignore_permissions=True)

    def _make_pr(
        self, supplier, branch, item_code, days_ago, gst_per=None, mrp=None,
        is_return=0, docstatus=1, extra_row_fields=None,
    ):
        row = {
            "item_code": item_code,
            "qty": 10,
            "rate": 100,
            "warehouse": branch.finished_goods_warehouse,
            "cost_center": branch.cost_center,
        }
        if gst_per is not None:
            row["custom_gst_per"] = gst_per
        if mrp is not None:
            row["custom_mrp"] = mrp
        if extra_row_fields:
            row.update(extra_row_fields)

        pr = frappe.get_doc({
            "doctype": "Purchase Receipt",
            "supplier": supplier,
            "company": branch.company,
            "branch": branch.name,
            "posting_date": add_days(nowdate(), -days_ago),
            "is_return": is_return,
            "items": [row],
        })
        pr.insert(ignore_permissions=True)
        if docstatus == 1:
            pr.submit()
        elif docstatus == 2:
            pr.submit()
            pr.cancel()
        return pr

    def _make_draft(self, supplier, branch, item_code, extra_row_fields=None):
        row = {
            "item_code": item_code,
            "qty": 5,
            "rate": 99,
            "warehouse": branch.finished_goods_warehouse,
            "cost_center": branch.cost_center,
        }
        if extra_row_fields:
            row.update(extra_row_fields)
        return frappe.get_doc({
            "doctype": "Purchase Receipt",
            "supplier": supplier,
            "company": branch.company,
            "branch": branch.name,
            "posting_date": nowdate(),
            "items": [row],
        })


class TestFieldDiscovery(_PHATestCase):
    """Schema-driven field discovery - covers "custom fields differ across
    SIZL, SZL, and HSM" without needing to run on all three sites at once:
    proves the function reflects whatever this site's real meta says,
    rather than a hardcoded list, and that a field simply absent from meta
    is simply absent from the result (never a KeyError)."""

    def test_discovered_fields_are_real_and_scalar(self):
        fields = get_autofillable_fields("Purchase Receipt Item")
        meta = frappe.get_meta("Purchase Receipt Item")
        real_fieldnames = {df.fieldname for df in meta.fields}
        for fieldname in fields:
            self.assertIn(fieldname, real_fieldnames)
            self.assertTrue(fieldname.startswith("custom_"))

    def test_qty_rate_and_computed_outputs_are_never_included(self):
        fields = get_autofillable_fields("Purchase Receipt Item")
        for protected in ("qty", "rate", "price_list_rate", "custom_vendor_rate", "amount", "branch", "warehouse"):
            self.assertNotIn(protected, fields)
        for computed in (
            "custom_price_after_taxes", "custom_old_price_after_tax", "custom_gross_total",
            "custom_line_total", "custom_gst_amount", "custom_advance_tax_amount", "custom_fed_amount",
        ):
            self.assertNotIn(computed, fields)

    def test_missing_doctype_field_gracefully_yields_nothing(self):
        """If a target doctype simply has no custom fields at all (a stand-in
        for 'this site's schema differs'), the function must return {} and
        never raise."""
        fields = get_autofillable_fields("Currency")  # a core doctype with no custom_* fields
        self.assertEqual(fields, {})


class TestApplyHistoryToRow(unittest.TestCase):
    """Pure logic, no DB - the "only if empty/null/zero" rule (rule 2)."""

    class _FakeRow:
        def __init__(self, **kwargs):
            self._data = dict(kwargs)

        def get(self, fieldname):
            return self._data.get(fieldname)

        def set(self, fieldname, value):
            self._data[fieldname] = value

    def test_fills_empty_none_and_zero_fields(self):
        row = self._FakeRow(custom_mrp=0, custom_gst_per=None, custom_discount_per="")
        changed = apply_history_to_row(row, {"custom_mrp": 150, "custom_gst_per": 17, "custom_discount_per": 5})
        self.assertEqual(set(changed), {"custom_mrp", "custom_gst_per", "custom_discount_per"})
        self.assertEqual(row.get("custom_mrp"), 150)

    def test_never_overwrites_existing_nonzero_value(self):
        row = self._FakeRow(custom_mrp=200)
        changed = apply_history_to_row(row, {"custom_mrp": 150})
        self.assertEqual(changed, [])
        self.assertEqual(row.get("custom_mrp"), 200)

    def test_deny_list_excludes_qty_and_rate_by_construction(self):
        self.assertIn("qty", _ALWAYS_EXCLUDE)
        self.assertIn("rate", _ALWAYS_EXCLUDE)
        self.assertIn("custom_vendor_rate", _ALWAYS_EXCLUDE)


class TestHistoryMatching(_PHATestCase):
    @_require_branch
    def test_item_exists_in_latest_purchase_same_supplier_branch(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=10, gst_per=17, mrp=150)
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=18, mrp=160)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        row = draft.items[0]
        self.assertEqual(row.custom_gst_per, 18.0)
        self.assertEqual(row.custom_mrp, 160.0)

    @_require_branch
    def test_item_exists_only_in_older_purchase(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=20, gst_per=15, mrp=140)
        # A newer purchase from the same supplier+branch exists, but for a
        # different item - ITEM_A's most recent appearance is still the
        # older document, and that's what must be picked (rule 5).
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_B, days_ago=1, gst_per=99, mrp=999)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        row = draft.items[0]
        self.assertEqual(row.custom_gst_per, 15.0)
        self.assertEqual(row.custom_mrp, 140.0)

    @_require_branch
    def test_item_same_supplier_different_branch_not_used(self):
        if not _OTHER_BRANCH:
            raise unittest.SkipTest("No second fully-configured Branch found on this site")
        self._make_pr(SUPPLIER_A, _OTHER_BRANCH, ITEM_A, days_ago=1, gst_per=25, mrp=500)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        row = draft.items[0]
        self.assertIsNone(row.custom_gst_per)
        self.assertIsNone(row.custom_mrp)

    @_require_branch
    def test_item_same_branch_different_supplier_not_used(self):
        self._make_pr(SUPPLIER_B, _BRANCH, ITEM_A, days_ago=1, gst_per=25, mrp=500)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        row = draft.items[0]
        self.assertIsNone(row.custom_gst_per)
        self.assertIsNone(row.custom_mrp)

    @_require_branch
    def test_item_never_purchased_leaves_fields_empty_and_does_not_block(self):
        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        # Must not raise (rule 18) even with zero purchase history.
        autofill_purchase_receipt_item_fields(draft)
        row = draft.items[0]
        self.assertIsNone(row.custom_gst_per)
        self.assertIsNone(row.custom_mrp)

    @_require_branch
    def test_multiple_items_different_histories(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=5, gst_per=17, mrp=150)
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_B, days_ago=3, gst_per=8, mrp=60)

        draft = frappe.get_doc({
            "doctype": "Purchase Receipt",
            "supplier": SUPPLIER_A,
            "company": _BRANCH.company,
            "branch": _BRANCH.name,
            "posting_date": nowdate(),
            "items": [
                {"item_code": ITEM_A, "qty": 5, "rate": 99, "warehouse": _BRANCH.finished_goods_warehouse, "cost_center": _BRANCH.cost_center},
                {"item_code": ITEM_B, "qty": 2, "rate": 40, "warehouse": _BRANCH.finished_goods_warehouse, "cost_center": _BRANCH.cost_center},
            ],
        })
        autofill_purchase_receipt_item_fields(draft)
        self.assertEqual(draft.items[0].custom_gst_per, 17.0)
        self.assertEqual(draft.items[0].custom_mrp, 150.0)
        self.assertEqual(draft.items[1].custom_gst_per, 8.0)
        self.assertEqual(draft.items[1].custom_mrp, 60.0)

    @_require_branch
    def test_quantity_and_rate_never_overwritten(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        original_qty, original_rate = draft.items[0].qty, draft.items[0].rate
        autofill_purchase_receipt_item_fields(draft)
        self.assertEqual(draft.items[0].qty, original_qty)
        self.assertEqual(draft.items[0].rate, original_rate)

    @_require_branch
    def test_existing_nonempty_values_not_overwritten(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150)

        draft = self._make_draft(
            SUPPLIER_A, _BRANCH, ITEM_A,
            extra_row_fields={"custom_gst_per": 5, "custom_discount_per": 2},
        )
        autofill_purchase_receipt_item_fields(draft)
        row = draft.items[0]
        self.assertEqual(row.custom_gst_per, 5)  # untouched - was already non-empty
        self.assertEqual(row.custom_mrp, 150.0)  # filled - was empty

    @_require_branch
    def test_cancelled_transactions_ignored(self):
        cancelled = self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150, docstatus=2)
        self.assertEqual(cancelled.docstatus, 2)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        self.assertIsNone(draft.items[0].custom_gst_per)

    @_require_branch
    def test_return_transactions_ignored(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150, is_return=1)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        self.assertIsNone(draft.items[0].custom_gst_per)

    @_require_branch
    def test_draft_source_documents_ignored(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150, docstatus=0)

        draft = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(draft)
        self.assertIsNone(draft.items[0].custom_gst_per)


class TestCalculationsUnaffected(_PHATestCase):
    @_require_branch
    def test_totals_identical_with_and_without_history_match(self):
        """The autofilled fields feed the client-side tax-calc script, not
        core ERPNext's own tax/total engine - so grand_total must be
        identical whether or not a history match exists, since qty/rate
        (the only inputs core ERPNext's calculate_taxes_and_totals uses)
        are never touched either way."""
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150)

        with_history = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        autofill_purchase_receipt_item_fields(with_history)
        with_history.insert(ignore_permissions=True)

        without_history = self._make_draft(SUPPLIER_B, _BRANCH, ITEM_B)
        without_history.insert(ignore_permissions=True)

        self.assertEqual(with_history.items[0].qty, without_history.items[0].qty)
        self.assertEqual(with_history.items[0].rate, without_history.items[0].rate)
        self.assertEqual(with_history.grand_total, with_history.items[0].qty * with_history.items[0].rate)
        self.assertEqual(without_history.grand_total, without_history.items[0].qty * without_history.items[0].rate)


class TestMapperIntegration(_PHATestCase):
    """Exercises the real hooks.py wiring (not just calling the function
    directly), through both entry points required by the spec: "Get Items
    From Purchase Order" and manually adding a Purchase Receipt row."""

    @_require_branch
    def test_hook_fires_via_get_items_from_purchase_order(self):
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150)

        po = frappe.get_doc({
            "doctype": "Purchase Order",
            "supplier": SUPPLIER_A,
            "company": _BRANCH.company,
            "branch": _BRANCH.name,
            "schedule_date": nowdate(),
            "items": [{
                "item_code": ITEM_A, "qty": 5, "rate": 99,
                "schedule_date": nowdate(),
            }],
        })
        po.insert(ignore_permissions=True)
        po.submit()

        mapped_pr = make_purchase_receipt(po.name)
        mapped_pr.insert(ignore_permissions=True)  # triggers before_validate -> our hook, for real

        row = mapped_pr.items[0]
        self.assertEqual(row.qty, 5)
        self.assertEqual(row.rate, 99)
        self.assertEqual(row.custom_gst_per, 17.0)
        self.assertEqual(row.custom_mrp, 150.0)

    @_require_branch
    def test_hook_fires_for_manually_added_item(self):
        self._make_pr(SUPPLIER_A, _BRANCH, ITEM_A, days_ago=1, gst_per=17, mrp=150)

        pr = self._make_draft(SUPPLIER_A, _BRANCH, ITEM_A)
        pr.insert(ignore_permissions=True)  # triggers before_validate -> our hook, for real

        row = pr.items[0]
        self.assertEqual(row.custom_gst_per, 17.0)
        self.assertEqual(row.custom_mrp, 150.0)
