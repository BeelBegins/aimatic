"""Unit tests for vendor stock incl-tax estimates (no DB)."""

from __future__ import annotations

import unittest

from aimatic.vendor_performance.api import _estimate_stock_value_incl_tax


class TestEstimateStockValueInclTax(unittest.TestCase):
	def test_uses_last_purchase_incl_rate_times_qty(self):
		incl, tax, basis = _estimate_stock_value_incl_tax(
			stock_qty=10,
			stock_value=1000,
			last_purchase_rate=100,
			last_purchase_rate_incl_tax=117,
		)
		self.assertEqual(basis, "last_purchase_rate_incl_tax")
		self.assertAlmostEqual(incl, 1170)
		self.assertAlmostEqual(tax, 170)

	def test_scales_bin_value_by_purchase_tax_factor(self):
		incl, tax, basis = _estimate_stock_value_incl_tax(
			stock_qty=0,
			stock_value=1000,
			last_purchase_rate=100,
			last_purchase_rate_incl_tax=117,
		)
		self.assertEqual(basis, "last_purchase_tax_factor")
		self.assertAlmostEqual(incl, 1170)
		self.assertAlmostEqual(tax, 170)

	def test_falls_back_to_item_fbr_rate(self):
		incl, tax, basis = _estimate_stock_value_incl_tax(
			stock_qty=5,
			stock_value=1000,
			fbr_tax_rate=17,
		)
		self.assertEqual(basis, "item_fbr_tax_rate")
		self.assertAlmostEqual(incl, 1170)
		self.assertAlmostEqual(tax, 170)

	def test_at_cost_when_no_tax_signal(self):
		incl, tax, basis = _estimate_stock_value_incl_tax(stock_qty=5, stock_value=1000)
		self.assertEqual(basis, "at_cost")
		self.assertAlmostEqual(incl, 1000)
		self.assertAlmostEqual(tax, 0)


if __name__ == "__main__":
	unittest.main()
