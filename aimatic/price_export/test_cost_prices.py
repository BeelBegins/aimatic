import unittest

from aimatic.price_export.api import _inclusive_rate_from_exclusive


class TestBranchPriceSheetCostPrices(unittest.TestCase):
	def test_reconstructs_migration_tax_inclusive_cost(self):
		self.assertEqual(_inclusive_rate_from_exclusive(507.44, 18), 598.7792)

	def test_exempt_cost_passes_through(self):
		self.assertEqual(_inclusive_rate_from_exclusive(507.44, 0), 507.44)

	def test_zero_cost_passes_through(self):
		self.assertEqual(_inclusive_rate_from_exclusive(0, 18), 0)


if __name__ == "__main__":
	unittest.main()
