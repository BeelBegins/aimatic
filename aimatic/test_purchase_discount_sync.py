import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aimatic.purchase_discount_sync import (
	apply_discount_per_from_source,
	apply_implied_discount_per,
	implied_discount_per,
	sync_purchase_invoice_discounts,
	sync_purchase_order_discounts,
	sync_purchase_receipt_discounts,
)


class TestImpliedDiscountPer(unittest.TestCase):
	def test_from_discount_amount(self):
		# ACC-PINV-2026-00200 / MAT-PRE-2026-00157 item 7 shape
		self.assertAlmostEqual(
			implied_discount_per(
				vendor_rate=374.58,
				rate=280.94,
				discount_amount=93.64,
			),
			(93.64 / 374.58) * 100.0,
			places=4,
		)

	def test_from_rate_gap_when_no_discount_amount(self):
		self.assertAlmostEqual(
			implied_discount_per(vendor_rate=100, rate=75, discount_amount=0),
			25.0,
			places=4,
		)

	def test_skips_rate_gap_when_scheme_qty(self):
		self.assertEqual(
			implied_discount_per(vendor_rate=100, rate=80, scheme_qty=1),
			0.0,
		)

	def test_discount_amount_wins_over_scheme_qty(self):
		self.assertAlmostEqual(
			implied_discount_per(
				vendor_rate=374.58,
				rate=280.94,
				discount_amount=93.64,
				scheme_qty=1,
			),
			(93.64 / 374.58) * 100.0,
			places=4,
		)

	def test_skips_when_trade_offer(self):
		self.assertEqual(
			implied_discount_per(vendor_rate=100, rate=80, trade_offer_total=10),
			0.0,
		)

	def test_skips_when_fed(self):
		self.assertEqual(
			implied_discount_per(vendor_rate=100, rate=80, fed_per=1),
			0.0,
		)

	def test_no_gap(self):
		self.assertEqual(implied_discount_per(vendor_rate=100, rate=100), 0.0)


class TestApplyToRow(unittest.TestCase):
	def test_does_not_overwrite_existing_percent(self):
		row = SimpleNamespace(
			custom_discount_per=7,
			custom_vendor_rate=100,
			rate=50,
			discount_amount=50,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
			get=lambda k, d=None: getattr(row, k, d),
		)
		# SimpleNamespace.get needs binding
		row.get = lambda k, d=None: getattr(row, k, d)
		self.assertEqual(apply_implied_discount_per(row), 7)
		self.assertEqual(row.custom_discount_per, 7)

	def test_sets_orphan_percent(self):
		row = SimpleNamespace(
			custom_discount_per=0,
			custom_vendor_rate=374.58,
			price_list_rate=374.58,
			rate=280.94,
			discount_amount=93.64,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		row.get = lambda k, d=None: getattr(row, k, d)
		applied = apply_implied_discount_per(row)
		self.assertGreater(applied, 24)
		self.assertLess(applied, 26)
		self.assertEqual(row.custom_discount_per, applied)


class TestInvoiceFromReceipt(unittest.TestCase):
	@patch("aimatic.purchase_discount_sync.frappe.db.get_value")
	def test_pi_inherits_orphan_discount_from_pr(self, get_value):
		get_value.return_value = SimpleNamespace(
			custom_discount_per=0,
			custom_vendor_rate=374.58,
			price_list_rate=374.58,
			rate=280.94,
			discount_amount=93.64,
			custom_scheme_qty=1,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		# PI row already wiped to vendor rate (client preview)
		row = SimpleNamespace(
			pr_detail="pr-item-1",
			custom_discount_per=0,
			custom_vendor_rate=374.58,
			price_list_rate=374.58,
			rate=374.58,
			discount_amount=0,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		row.get = lambda k, d=None: getattr(row, k, d)
		doc = SimpleNamespace(docstatus=0, items=[row], get=lambda k, d=None: getattr(doc, k, d))

		sync_purchase_invoice_discounts(doc)

		self.assertGreater(row.custom_discount_per, 24)
		self.assertLess(row.custom_discount_per, 26)

	@patch("aimatic.purchase_discount_sync.frappe.db.get_value")
	def test_pi_prefers_pr_custom_discount_per(self, get_value):
		get_value.return_value = SimpleNamespace(
			custom_discount_per=7,
			custom_vendor_rate=100,
			price_list_rate=100,
			rate=93,
			discount_amount=0,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		row = SimpleNamespace(
			pr_detail="pr-item-2",
			custom_discount_per=0,  # autofill miss / wipe
			custom_vendor_rate=100,
			rate=100,
			discount_amount=0,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		row.get = lambda k, d=None: getattr(row, k, d)
		doc = SimpleNamespace(docstatus=0, items=[row], get=lambda k, d=None: getattr(doc, k, d))

		sync_purchase_invoice_discounts(doc)
		self.assertEqual(row.custom_discount_per, 7)

	def test_submitted_docs_untouched(self):
		row = SimpleNamespace(
			pr_detail=None,
			custom_discount_per=0,
			custom_vendor_rate=100,
			rate=80,
			discount_amount=20,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		row.get = lambda k, d=None: getattr(row, k, d)
		doc = SimpleNamespace(docstatus=1, items=[row], get=lambda k, d=None: getattr(doc, k, d))
		sync_purchase_invoice_discounts(doc)
		sync_purchase_receipt_discounts(doc)
		sync_purchase_order_discounts(doc)
		self.assertEqual(row.custom_discount_per, 0)


class TestSourceApply(unittest.TestCase):
	def test_source_none_falls_back_to_row(self):
		row = SimpleNamespace(
			custom_discount_per=0,
			custom_vendor_rate=100,
			rate=90,
			discount_amount=10,
			custom_scheme_qty=0,
			custom_trade_offer_total=0,
			custom_fed_per=0,
			custom_fed_amount=0,
		)
		row.get = lambda k, d=None: getattr(row, k, d)
		self.assertEqual(apply_discount_per_from_source(row, None), 10.0)


if __name__ == "__main__":
	unittest.main()
