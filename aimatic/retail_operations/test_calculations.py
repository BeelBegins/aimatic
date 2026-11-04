from datetime import date
from unittest import TestCase

from aimatic.retail_operations.calculations import (
	cadence,
	chunks,
	recommend,
	reference_candidates,
	vendor_packs,
	webhook_valid,
)


class CalculationsTest(TestCase):
	def row(self, **kw):
		return dict(
			item_code="A",
			item_name="Soap 100g",
			supplier="Vendor",
			stock_uom="Pcs",
			actual_qty=0,
			sales_qty=280,
			recent_sales_qty=70,
			**kw,
		)

	def test_one_piece_is_not_low_when_demand_is_small(self):
		row = self.row()
		row.update(actual_qty=1, sales_qty=0.1, recent_sales_qty=0)
		result = recommend(row, [])
		self.assertEqual(result["status"], "Overstock")
		self.assertEqual(result["suggested_qty"], 0)

	def test_large_stock_can_be_low_cover(self):
		row = self.row()
		row.update(actual_qty=40, sales_qty=560, recent_sales_qty=140)
		self.assertEqual(recommend(row, [])["status"], "Low cover")

	def test_negative_balance_does_not_auto_order(self):
		row = self.row()
		row["actual_qty"] = -200
		result = recommend(row, [])
		self.assertFalse(result["auto_eligible"])
		self.assertTrue(result["flags"])

	def test_unknown_eta_blocks_duplicate_order(self):
		row = self.row(unknown_supply_qty=100)
		self.assertFalse(recommend(row, [])["auto_eligible"])

	def test_outstanding_request_is_not_confirmed_stock(self):
		row = self.row(requested_qty=100)
		result = recommend(row, [])
		self.assertEqual(result["available_qty"], 0)
		self.assertFalse(result["auto_eligible"])

	def test_confirmed_supply_reduces_new_requirement(self):
		a = recommend(self.row(), [])
		b = recommend(self.row(open_supply_qty=30), [])
		self.assertEqual(a["suggested_qty"] - b["suggested_qty"], 30)

	def test_partial_receipts_do_not_invent_supplier_lead(self):
		history = [
			dict(date="2026-10-01", qty=10, purchase_order="PO1"),
			dict(date="2026-10-02", qty=10, purchase_order="PO1"),
			dict(date="2026-10-08", qty=10, purchase_order="PO2"),
			dict(date="2026-10-15", qty=10, purchase_order="PO3"),
		]
		learned = cadence(history)
		self.assertEqual(learned["days"], 7)
		self.assertIsNone(learned["lead_time_days"])

	def test_exceptional_gap_does_not_become_order_cycle(self):
		history = [dict(date=d, qty=10) for d in ["2026-09-01", "2026-09-08", "2026-09-15", "2026-10-06"]]
		self.assertEqual(cadence(history)["days"], 7)
		self.assertEqual(cadence(history)["exception_gaps"], [21])

	def test_disabled_items_never_auto_order(self):
		self.assertFalse(recommend(self.row(disabled=1), [])["auto_eligible"])

	def test_reference_detection_does_not_merge_variants(self):
		rows = [
			dict(item_code="a", item_name="Soap 100g", brand="A", stock_uom="Pcs", actual_qty=-2),
			dict(item_code="b", item_name="Soap 200g", brand="A", stock_uom="Pcs", actual_qty=20),
		]
		self.assertEqual(reference_candidates(rows), [])
		rows[1]["item_name"] = "Soap 100g"
		self.assertEqual(len(reference_candidates(rows)), 1)

	def test_vendor_keeps_uncertain_lines_separate(self):
		packs = vendor_packs(
			[recommend(self.row(), []), recommend(self.row(flags=["Reference mismatch"]), [])]
		)
		self.assertEqual(len(packs[0]["ready"]), 1)
		self.assertEqual(len(packs[0]["review"]), 1)

	def test_webhook_cannot_redirect_to_arbitrary_host(self):
		self.assertTrue(webhook_valid("https://discord.com/api/webhooks/123/example-placeholder"))
		for address in [
			"http://discord.com/api/webhooks/123/x",
			"https://discord.com.evil.test/api/webhooks/123/x",
			"https://localhost/api/webhooks/123/x",
			"https://discord.com/api/webhooks/123/x?redirect=x",
		]:
			self.assertFalse(webhook_valid(address))

	def test_discord_page_limits_preserve_all_content(self):
		parts = chunks(["a" * 2000, "b" * 2000])
		self.assertTrue(all(len(p) <= 1800 for p in parts))
		self.assertEqual(sum(p.count("a") for p in parts), 2000)
		self.assertEqual(sum(p.count("b") for p in parts), 2000)

	def test_hub_procurement_covers_outgoing_commitments_without_inventing_sales(self):
		row = self.row(outgoing_transfer_qty=20)
		row.update(actual_qty=5, sales_qty=0, recent_sales_qty=0)
		result = recommend(row, [])
		self.assertEqual(result["suggested_qty"], 15)
		self.assertTrue(result["auto_eligible"])

	def test_fresh_goods_use_bounded_cover(self):
		row = self.row()
		result = recommend(
			row, [dict(date=d, qty=10) for d in ("2026-10-01", "2026-10-08", "2026-10-15")], category_days=2
		)
		self.assertLessEqual(result["target_qty"], result["daily_demand"] * 2)

	def test_walk_forward_coverage_replay_uses_prior_demand(self):
		from datetime import timedelta

		from aimatic.retail_operations.calculations import replay_coverage

		start = date(2026, 9, 1)
		history = [dict(date=d, qty=70) for d in ("2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29")]
		daily = [dict(date=str(start + timedelta(days=n)), qty=10) for n in range(60)]
		result = replay_coverage(history, daily)
		self.assertEqual(result["selected_days"], 7)
		self.assertGreaterEqual(result["cycles"], 2)
		daily[-1]["qty"] = 99999
		self.assertEqual(replay_coverage(history, daily)["selected_days"], 7)

	def test_negative_stock_is_visible_even_without_recent_sales(self):
		row = self.row()
		row.update(actual_qty=-10, sales_qty=0, recent_sales_qty=0)
		result = recommend(row, [])
		self.assertEqual(result["status"], "Negative stock")
		self.assertEqual(vendor_packs([result]), [])
