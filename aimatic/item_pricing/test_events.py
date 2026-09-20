import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aimatic.item_pricing.events import update_latest_price_incl_taxes


def _doc(is_return=0):
	row = SimpleNamespace(
		item_code="ITEM-1", get=lambda k, d=None: {"custom_price_after_taxes": 91.66}.get(k, d)
	)
	return SimpleNamespace(is_return=is_return, posting_date="2026-09-18", items=[row])


class TestLatestPriceCostHook(unittest.TestCase):
	@patch("aimatic.item_pricing.events.frappe")
	def test_return_does_not_touch_prices(self, frappe):
		update_latest_price_incl_taxes(_doc(is_return=1))
		frappe.db.set_value.assert_not_called()

	@patch("aimatic.item_pricing.events.frappe")
	def test_purchase_updates_cost_without_bumping_modified(self, frappe):
		frappe.db.get_value.return_value = None
		update_latest_price_incl_taxes(_doc())
		item_price_call = next(c for c in frappe.db.set_value.call_args_list if c.args[0] == "Item Price")
		self.assertEqual(item_price_call.args[2], "custom_latest_price_incl_taxes")
		self.assertIs(item_price_call.kwargs.get("update_modified"), False)


if __name__ == "__main__":
	unittest.main()
