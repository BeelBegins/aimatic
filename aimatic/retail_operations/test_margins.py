from unittest import TestCase

from aimatic.retail_operations.margins import summarize


def line(code="A", sales=100, cost=80, **extra):
	return dict(
		item_code=code,
		item_name=code,
		sales=sales,
		cogs=cost,
		stock_qty=1,
		is_return=0,
		has_ledger_cogs=1,
		consolidated_item=code,
		**extra,
	)


class MarginTest(TestCase):
	def test_zero_cost_is_not_a_winning_margin(self):
		r = summarize([line(cost=0)], {}, {})
		self.assertEqual(r["highest"], [])
		self.assertIn("Zero recorded cost", str(r["exceptions"]))
		self.assertEqual(r["exceptions"][0]["cogs"], 0)

	def test_negative_stock_and_high_margin_held_for_review(self):
		r = summarize([line(cost=25)], {"A": {"minimum_balance": -2}}, {})
		self.assertEqual(r["eligible_count"], 0)
		self.assertIn("negative stock", str(r["exceptions"]))
		self.assertIn("Margin above 60%", str(r["exceptions"]))

	def test_receipt_discrepancy_preserves_recorded_loss(self):
		r = summarize([line(sales=110.39, cost=193.46)], {}, {"A": dict(unit_cost=95, date="2026-10-21")})
		self.assertEqual(r["negative"], [])
		self.assertEqual(r["exceptions"][0]["profit"], -83.07)
		self.assertEqual(r["exceptions"][0]["receipt_unit_cost"], 95)

	def test_negative_and_lowest_lists_are_disjoint(self):
		r = summarize([line("A", cost=110), line("B", cost=90), line("C", cost=60)], {}, {})
		self.assertEqual([i["item_code"] for i in r["negative"]], ["A"])
		self.assertEqual([i["item_code"] for i in r["lowest"]], ["B", "C"])

	def test_return_disclosed_with_recorded_net_effect(self):
		returned = line(sales=-100, cost=-80)
		returned.update(is_return=1, stock_qty=-1)
		r = summarize([line(), returned], {}, {})
		self.assertEqual(r["highest"][0]["return_qty"], 1)
		self.assertEqual(r["returns"][0]["net_sales"], 0)
		self.assertEqual(r["returns"][0]["net_profit"], 0)

	def test_incomplete_item_does_not_rank_covered_subset(self):
		pending = line()
		pending.update(has_ledger_cogs=0, cogs=None)
		r = summarize([line(), pending], {}, {})
		self.assertEqual(r["highest"], [])
		self.assertEqual(r["pending_cost_lines"], 1)
		self.assertIsNone(r["exceptions"][0]["profit"])
		self.assertIsNone(r["exceptions"][0]["cogs"])

	def test_returns_only_item_does_not_enter_sales_rankings(self):
		returned = line(sales=-100, cost=-80)
		returned.update(is_return=1, stock_qty=-1)
		r = summarize([returned], {}, {})
		self.assertEqual(r["highest"], [])
		self.assertEqual(r["returns"][0]["net_profit"], -20)

	def test_extreme_rate_screening_is_explicit_not_a_cost_replacement(self):
		r = summarize([line(cost=20)], {}, {}, receipt_limited=True)
		self.assertEqual(r["exceptions"][0]["cogs"], 20)
		self.assertTrue(r["receipt_comparison_limited"])
		self.assertIn("60%", r["note"])
