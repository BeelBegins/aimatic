import unittest
from unittest import mock

import frappe

from aimatic.fbr_pos.accounting import adjust_cash_payment_to_grand_total


def _doc(grand_total, payments, loyalty_amount=0, redeem=0):
	return frappe._dict(
		grand_total=grand_total,
		is_return=0,
		pos_profile=None,
		redeem_loyalty_points=redeem,
		loyalty_amount=loyalty_amount,
		payments=[frappe._dict(mode_of_payment="Cash", amount=a) for a in payments],
	)


def _plain_flt(value, precision=None):
	# frappe.utils.flt(value, precision) needs a connected site to round.
	return round(float(value or 0), 2 if precision is None else precision)


@mock.patch("aimatic.fbr_pos.accounting.flt", _plain_flt)
class TestAdjustCashPaymentLoyalty(unittest.TestCase):
	"""No database: adjust_cash_payment_to_grand_total only reads the doc."""

	def test_reduced_cash_row_is_kept_when_points_are_redeemed(self):
		doc = _doc(356, [256], loyalty_amount=100, redeem=1)
		adjust_cash_payment_to_grand_total(doc)
		self.assertEqual(doc.payments[0].amount, 256)
		# ERPNext counts the redemption as paid: validate_full_payment needs
		# paid_amount >= grand_total or submit fails with a 417.
		self.assertEqual(doc.paid_amount, 356)
		self.assertEqual(doc.change_amount, 0)

	def test_full_tender_on_a_redemption_owes_the_loyalty_amount_as_change(self):
		doc = _doc(356, [356], loyalty_amount=100, redeem=1)
		adjust_cash_payment_to_grand_total(doc)
		self.assertEqual(doc.paid_amount, 456)
		self.assertEqual(doc.change_amount, 100)

	def test_loyalty_amount_is_ignored_unless_redemption_is_flagged(self):
		doc = _doc(356, [356], loyalty_amount=100, redeem=0)
		adjust_cash_payment_to_grand_total(doc)
		self.assertEqual(doc.change_amount, 0)

	def test_plain_sale_change_is_unchanged(self):
		doc = _doc(356, [400])
		adjust_cash_payment_to_grand_total(doc)
		self.assertEqual(doc.paid_amount, 400)
		self.assertEqual(doc.change_amount, 44)

	def test_single_implicit_payment_aligns_to_payable(self):
		doc = _doc(356, [10], loyalty_amount=100, redeem=1)
		adjust_cash_payment_to_grand_total(doc)
		self.assertEqual(doc.payments[0].amount, 256)
		self.assertEqual(doc.paid_amount, 356)
		self.assertEqual(doc.change_amount, 0)


@mock.patch("aimatic.loyalty.events.flt", _plain_flt)
class TestRedemptionHonored(unittest.TestCase):
	def _honored(self, grand_total, loyalty_amount, payments, change=0):
		from aimatic.loyalty.events import _redemption_was_actually_honored

		doc = frappe._dict(
			grand_total=grand_total,
			loyalty_amount=loyalty_amount,
			change_amount=change,
			payments=[frappe._dict(amount=a) for a in payments],
		)
		return _redemption_was_actually_honored(doc)

	def test_reduced_payment_is_honored(self):
		self.assertTrue(self._honored(356, 100, [256]))

	def test_full_tender_with_change_back_is_honored(self):
		self.assertTrue(self._honored(356, 100, [356], change=100))

	def test_full_price_kept_is_not_honored(self):
		self.assertFalse(self._honored(356, 100, [356]))


@mock.patch("aimatic.loyalty.paid_amount.flt", _plain_flt)
class TestIncludeLoyaltyInPaidAmount(unittest.TestCase):
	def _run(self, payments, loyalty_amount, redeem=1, is_return=0):
		from aimatic.loyalty.paid_amount import include_loyalty_in_paid_amount

		doc = frappe._dict(
			is_return=is_return,
			redeem_loyalty_points=redeem,
			loyalty_amount=loyalty_amount,
			conversion_rate=1,
			paid_amount=sum(payments),
			payments=[frappe._dict(amount=a) for a in payments],
		)
		include_loyalty_in_paid_amount(doc)
		return doc.paid_amount

	def test_redemption_paid_amount_counts_the_loyalty_discount(self):
		self.assertEqual(self._run([115], 100), 215)

	def test_plain_sale_and_returns_are_untouched(self):
		self.assertEqual(self._run([215], 0, redeem=0), 215)
		self.assertEqual(self._run([-50], 20, is_return=1), -50)
