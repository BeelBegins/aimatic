from unittest import TestCase

from aimatic.branch_transfer_planner import plan_transfers


def _pos(branch, stock, sales, item="A", **extra):
	return {"item_code": item, "branch": branch, "stock_qty": stock, "sales_qty": sales, "history_days": 28, **extra}


class TestBranchTransferPlanner(TestCase):
	def test_stocked_out_branch_is_served_from_the_branch_with_surplus(self):
		rows = plan_transfers(
			[_pos("S1", 0, 28), _pos("S2", 400, 28, valuation_rate=10)], target_cover_days=7, donor_keep_days=30
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["receiver_branch"], "S1")
		self.assertEqual(rows[0]["donor_branch"], "S2")
		self.assertEqual(rows[0]["priority"], "Stock-out")
		self.assertEqual(rows[0]["suggested_qty"], 7)
		self.assertEqual(rows[0]["est_value"], 70)

	def test_donor_is_never_taken_below_its_own_kept_cover(self):
		# S2 sells 1/day and keeps 30 days: only 5 of its 35 units are spare.
		rows = plan_transfers([_pos("S1", 0, 140), _pos("S2", 35, 28)], target_cover_days=7, donor_keep_days=30)
		self.assertEqual(rows[0]["suggested_qty"], 5)

	def test_one_donor_is_split_across_receivers_without_double_counting(self):
		rows = plan_transfers(
			[_pos("S1", 0, 28), _pos("S3", 0, 28), _pos("S2", 40, 0)], target_cover_days=7, donor_keep_days=30
		)
		self.assertEqual(sum(r["suggested_qty"] for r in rows), 14)
		self.assertLessEqual(sum(r["suggested_qty"] for r in rows), 40)

	def test_no_move_without_demand_or_without_a_donor(self):
		self.assertEqual(plan_transfers([_pos("S1", 0, 0), _pos("S2", 100, 0)]), [])
		self.assertEqual(plan_transfers([_pos("S1", 0, 28), _pos("S2", 10, 28)]), [])

	def test_negative_stock_is_never_a_donor(self):
		self.assertEqual(plan_transfers([_pos("S1", 0, 28), _pos("S2", -50, 0)]), [])

	def test_whole_number_items_are_rounded_down(self):
		rows = plan_transfers(
			[_pos("S1", 0, 20, whole_number=True), _pos("S2", 400, 0, whole_number=True)], target_cover_days=7
		)
		self.assertEqual(rows[0]["suggested_qty"], 5)
		# 21 sold in 28 days -> 5.25 needed; whole-number items get 5, weighed items keep 5.25
		rows = plan_transfers([_pos("S1", 0, 21, whole_number=True), _pos("S2", 400, 0, whole_number=True)])
		self.assertEqual(rows[0]["suggested_qty"], 5)
		rows = plan_transfers([_pos("S1", 0, 21), _pos("S2", 400, 0)])
		self.assertEqual(rows[0]["suggested_qty"], 5.25)
