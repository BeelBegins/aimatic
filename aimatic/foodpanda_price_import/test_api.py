import unittest

from openpyxl import Workbook

from aimatic.foodpanda_price_import.api import _build_import_plan


def _sheet(rows):
	workbook = Workbook()
	sheet = workbook.active
	sheet.append(["sku", "name", "price", "active", "barcode 1", "barcode 2", "barcode 14"])
	for row in rows:
		sheet.append(row)
	return sheet


class TestFoodpandaPriceImportPlan(unittest.TestCase):
	def test_matches_every_barcode_column_and_expands_multi_item_rows(self):
		barcode_map = {
			"00123": {"ITEM-1", "ITEM-2"},
			"999": {"ITEM-3"},
		}
		updates, stats, exceptions = _build_import_plan(
			_sheet(
				[
					["SKU-1", "One", 125, False, "00123", None, None],
					["SKU-2", "Two", 250, True, None, None, "999"],
				]
			),
			barcode_map,
		)

		self.assertEqual(
			updates,
			{"ITEM-1": {"price": 125.0}, "ITEM-2": {"price": 125.0}, "ITEM-3": {"price": 250.0}},
		)
		self.assertEqual(stats["multi_item_rows"], 1)
		self.assertEqual(stats["accepted_items"], 3)
		self.assertEqual(exceptions, [])

	def test_conflicts_choose_highest_active_price_not_higher_inactive_price(self):
		barcode_map = {"123": {"ITEM-1"}}
		updates, stats, exceptions = _build_import_plan(
			_sheet(
				[
					["ACTIVE", "Active row", 100, True, "123", None, None],
					["INACTIVE", "Inactive row", 150, False, "123", None, None],
				]
			),
			barcode_map,
		)

		self.assertEqual(updates, {"ITEM-1": {"price": 100.0}})
		self.assertEqual(stats["conflicting_items"], 1)
		self.assertEqual(stats["resolved_conflicts"], 1)
		self.assertEqual(stats["skipped_inactive_conflicts"], 0)
		self.assertEqual(exceptions, [])

	def test_conflict_with_only_inactive_rows_is_skipped_and_reported(self):
		barcode_map = {"123": {"ITEM-1"}}
		updates, stats, exceptions = _build_import_plan(
			_sheet(
				[
					["LOW", "Low", 100, False, "123", None, None],
					["HIGH", "High", 150, False, "123", None, None],
				]
			),
			barcode_map,
		)

		self.assertEqual(updates, {})
		self.assertEqual(stats["skipped_inactive_conflicts"], 1)
		self.assertEqual(exceptions[0]["item_code"], "ITEM-1")
		self.assertIn("all source rows inactive", exceptions[0]["reason"])

	def test_inactive_non_conflicting_row_is_imported(self):
		barcode_map = {"123": {"ITEM-1"}}
		updates, stats, _exceptions = _build_import_plan(
			_sheet([["SKU", "Product", 75, False, "123", None, None]]),
			barcode_map,
		)

		self.assertEqual(updates, {"ITEM-1": {"price": 75.0}})
		self.assertEqual(stats["accepted_items"], 1)

	def test_unmatched_and_bad_prices_are_not_planned(self):
		updates, stats, exceptions = _build_import_plan(
			_sheet(
				[
					["NO-MATCH", "No match", 50, True, "404", None, None],
					["BAD", "Bad price", 0, True, "123", None, None],
				]
			),
			{"123": {"ITEM-1"}},
		)

		self.assertEqual(updates, {})
		self.assertEqual(stats["unmatched"], 1)
		self.assertEqual(stats["skipped_bad_price"], 1)
		self.assertEqual(len(exceptions), 2)


if __name__ == "__main__":
	unittest.main()
