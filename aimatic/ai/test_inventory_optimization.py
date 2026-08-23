from unittest import TestCase

from aimatic.ai.inventory_optimization import calculate_transfers


class TestInventoryTransfers(TestCase):
	def test_surplus_is_matched_to_deficit_without_double_allocation(self):
		positions = [
			{
				"item_code": "A",
				"item_name": "A",
				"branch": "North",
				"warehouse": "N",
				"stock_quantity": 100,
				"daily_demand": 1,
				"valuation_rate": 10,
				"active_days": 30,
			},
			{
				"item_code": "A",
				"item_name": "A",
				"branch": "South",
				"warehouse": "S",
				"stock_quantity": 0,
				"daily_demand": 2,
				"valuation_rate": 10,
				"active_days": 30,
			},
			{
				"item_code": "A",
				"item_name": "A",
				"branch": "East",
				"warehouse": "E",
				"stock_quantity": 0,
				"daily_demand": 1,
				"valuation_rate": 10,
				"active_days": 30,
			},
		]
		rows = calculate_transfers(positions, target_cover_days=30)
		self.assertEqual(sum(row["transfer_qty"] for row in rows), 70)
		self.assertTrue(all(row["from_branch"] == "North" for row in rows))

	def test_different_items_are_never_cross_matched(self):
		rows = calculate_transfers(
			[
				{"item_code": "A", "branch": "North", "stock_quantity": 100, "daily_demand": 0},
				{"item_code": "B", "branch": "South", "stock_quantity": 0, "daily_demand": 5},
			]
		)
		self.assertEqual(rows, [])
