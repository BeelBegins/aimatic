import unittest
from unittest import mock

import frappe

from aimatic.loyalty import events


class TestEarningEntryLookup(unittest.TestCase):
	"""Regression: szl 2026-10-30..11-06. A redemption sale also carries core's
	redemption rows (invoice=<same>, redeem_against set); the point correction
	overwrote one of those and left the 1-point-per-rupee earning row alone."""

	def test_lookup_excludes_redemption_rows(self):
		with mock.patch.object(events, "frappe") as fr:
			fr.db.get_value.return_value = "EARN"
			self.assertEqual(events._earning_entry_name("PINV-1"), "EARN")
		filters = fr.db.get_value.call_args.args[1]
		self.assertEqual(filters["invoice"], "PINV-1")
		self.assertEqual(filters["invoice_type"], "POS Invoice")
		self.assertEqual(filters["redeem_against"], ["is", "not set"])

	def test_core_redemption_rows_removed_only_for_this_invoice(self):
		with mock.patch.object(events, "frappe") as fr:
			events._remove_core_redemption_entries("PINV-1")
		fr.db.delete.assert_called_once_with(
			"Loyalty Point Entry",
			{"invoice_type": "POS Invoice", "invoice": "PINV-1", "redeem_against": ["is", "set"]},
		)


class TestRedemptionSubmitHook(unittest.TestCase):
	def _doc(self, redeem=1, is_return=0):
		return frappe._dict(name="PINV-1", redeem_loyalty_points=redeem, is_return=is_return)

	def test_redemption_removes_core_rows_then_debits(self):
		calls = []
		with (
			mock.patch.object(
				events, "_remove_core_redemption_entries", side_effect=lambda n: calls.append(("rm", n))
			),
			mock.patch.object(
				events,
				"_debit_redeemed_loyalty_points",
				side_effect=lambda d: calls.append(("debit", d.name)),
			),
		):
			events.on_submit_debit_redeemed_loyalty_points(self._doc())
		self.assertEqual(calls, [("rm", "PINV-1"), ("debit", "PINV-1")])

	def test_plain_sale_and_return_leave_ledger_alone(self):
		with (
			mock.patch.object(events, "_remove_core_redemption_entries") as rm,
			mock.patch.object(events, "_debit_redeemed_loyalty_points"),
		):
			events.on_submit_debit_redeemed_loyalty_points(self._doc(redeem=0))
			events.on_submit_debit_redeemed_loyalty_points(self._doc(is_return=1))
		rm.assert_not_called()


if __name__ == "__main__":
	unittest.main()
