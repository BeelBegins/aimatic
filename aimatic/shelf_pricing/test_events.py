import unittest
from types import SimpleNamespace

from aimatic.shelf_pricing.events import (
	compute_shelf_gm_percent,
	compute_shelf_price_from_gm,
	reset_price_update_status_on_amend,
	set_shelf_gm_percent,
)


class TestResetPriceUpdateStatusOnAmend(unittest.TestCase):
	def test_amended_receipt_resets_inherited_updated_status(self):
		doc = SimpleNamespace(
			amended_from="MAT-PRE-2026-00034",
			custom_branch_price_update_status="Updated",
			custom_foodpanda_price_update_status="Skipped",
		)

		reset_price_update_status_on_amend(doc)

		self.assertEqual(doc.custom_branch_price_update_status, "Pending")
		self.assertEqual(doc.custom_foodpanda_price_update_status, "Pending")

	def test_non_amended_receipt_keeps_status(self):
		doc = SimpleNamespace(
			amended_from=None,
			custom_branch_price_update_status="Updated",
			custom_foodpanda_price_update_status="Updated",
		)

		reset_price_update_status_on_amend(doc)

		self.assertEqual(doc.custom_branch_price_update_status, "Updated")
		self.assertEqual(doc.custom_foodpanda_price_update_status, "Updated")


class TestShelfGmPercent(unittest.TestCase):
	def test_gm_percent_matches_print_layout_formula(self):
		# sale 125, cost 100 → (25/125)*100 = 20
		self.assertEqual(compute_shelf_gm_percent(125, 100), 20.0)

	def test_gm_percent_zero_when_sale_blank(self):
		self.assertEqual(compute_shelf_gm_percent(0, 100), 0.0)
		self.assertEqual(compute_shelf_gm_percent(None, 100), 0.0)

	def test_gm_percent_negative_when_sale_below_cost(self):
		self.assertEqual(compute_shelf_gm_percent(90, 100), -11.11)

	def test_set_shelf_gm_percent_updates_rows(self):
		row = SimpleNamespace(custom_shelf_price=200, custom_price_after_taxes=150, custom_gm_percent=None)
		doc = SimpleNamespace(docstatus=0, items=[row])

		set_shelf_gm_percent(doc)

		self.assertEqual(row.custom_gm_percent, 25.0)

	def test_set_shelf_gm_percent_skips_submitted(self):
		row = SimpleNamespace(custom_shelf_price=200, custom_price_after_taxes=150, custom_gm_percent=0.0)
		doc = SimpleNamespace(docstatus=1, items=[row])

		set_shelf_gm_percent(doc)

		self.assertEqual(row.custom_gm_percent, 0.0)

	def test_shelf_from_gm_round_whole_rupee(self):
		# cost 100, GM 20 → 125; cost 83.33, GM 20 → round(104.1625) = 104
		self.assertEqual(compute_shelf_price_from_gm(100, 20), 125.0)
		self.assertEqual(compute_shelf_price_from_gm(83.33, 20), 104.0)

	def test_shelf_from_gm_rejects_invalid_margin(self):
		self.assertIsNone(compute_shelf_price_from_gm(100, 100))
		self.assertIsNone(compute_shelf_price_from_gm(100, 120))

	def test_shelf_from_gm_requires_positive_cost_and_gm(self):
		self.assertIsNone(compute_shelf_price_from_gm(0, 25))
		self.assertIsNone(compute_shelf_price_from_gm(100, 0))
		self.assertIsNone(compute_shelf_price_from_gm(0, 0))

	def test_shelf_from_gm_exact_without_round(self):
		self.assertEqual(compute_shelf_price_from_gm(83.33, 20, round_whole=False), 104.16)
