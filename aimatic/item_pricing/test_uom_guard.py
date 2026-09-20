import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from aimatic.fbr_pos.tax_calculator import calculate_fbr_item
from aimatic.item_pricing import uom_guard

THIRD_SCHEDULE = {
	"tax_rate": 18,
	"is_exempt": 0,
	"is_zero_rated": 0,
	"is_third_schedule": 1,
	"mrp": 100,
}


def _row(**kw):
	base = dict(
		item_code="ITEM-1",
		idx=1,
		qty=1,
		uom="Pcs",
		stock_uom="Pcs",
		conversion_factor=1,
		rate=100,
		amount=100,
	)
	base.update(kw)
	return SimpleNamespace(get=lambda k, d=None, _b=base: _b.get(k, d), **base)


class TestThirdScheduleRowGuard(unittest.TestCase):
	def test_normal_piece_sale_passes(self):
		out = calculate_fbr_item(_row(), THIRD_SCHEDULE)
		self.assertGreater(out["value_excluding_tax"], 0)

	def test_box_sold_at_piece_price_is_blocked(self):
		# 28 Box (CF 28) at Rs 99: tax on MRP base (Rs 11,959) exceeds Rs 2,772.
		row = _row(qty=28, uom="Box", conversion_factor=28, rate=99, amount=2772)
		with self.assertRaises(frappe.ValidationError):
			calculate_fbr_item(row, THIRD_SCHEDULE)

	def test_correct_box_price_passes(self):
		row = _row(qty=28, uom="Box", conversion_factor=28, rate=2689, amount=75292)
		self.assertGreater(calculate_fbr_item(row, THIRD_SCHEDULE)["value_excluding_tax"], 0)


class TestUomPriceGuard(unittest.TestCase):
	def _doc(self, **row_kw):
		doc = SimpleNamespace(
			is_return=0,
			selling_price_list="S1 List",
			posting_date="2026-09-13",
			items=[_row(**row_kw)],
		)
		doc.get = lambda k, d=None: getattr(doc, k, d)
		return doc

	@patch.object(uom_guard, "get_stock_uom_price", return_value=99)
	def test_box_at_piece_price_blocked(self, _):
		doc = self._doc(uom="Box", conversion_factor=28, qty=28, rate=99)
		with self.assertRaises(frappe.ValidationError):
			uom_guard.validate_pos_uom_pricing(doc)

	@patch.object(uom_guard, "get_stock_uom_price", return_value=99)
	def test_small_box_discount_allowed(self, _):
		doc = self._doc(uom="Box", conversion_factor=28, qty=1, rate=2689)
		uom_guard.validate_pos_uom_pricing(doc)

	@patch.object(uom_guard, "get_stock_uom_price", return_value=99)
	def test_stock_uom_and_returns_skip(self, _):
		uom_guard.validate_pos_uom_pricing(self._doc())
		doc = self._doc(uom="Box", conversion_factor=28, rate=99)
		doc.is_return = 1
		uom_guard.validate_pos_uom_pricing(doc)

	@patch.object(uom_guard, "get_stock_uom_price", return_value=0)
	def test_no_stock_price_does_not_block(self, _):
		uom_guard.validate_pos_uom_pricing(self._doc(uom="Box", conversion_factor=28, rate=99))


if __name__ == "__main__":
	unittest.main()
