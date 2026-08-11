import unittest
from unittest.mock import patch

from aimatic.stock_reports.stock_ledger_barcodes import _with_barcodes


class TestStockLedgerBarcodes(unittest.TestCase):
	def test_inserts_barcodes_column_after_item_name(self):
		columns = [
			{"fieldname": "date"},
			{"fieldname": "item_code"},
			{"fieldname": "item_name"},
			{"fieldname": "stock_uom"},
		]
		data = [{"item_code": "ITEM-1", "item_name": "One"}]

		with patch(
			"aimatic.stock_reports.stock_ledger_barcodes._barcodes_by_item",
			return_value={"ITEM-1": "123, 456"},
		):
			columns, data = _with_barcodes(columns, data)

		self.assertEqual(columns[3]["fieldname"], "barcodes")
		self.assertEqual(data[0]["barcodes"], "123, 456")

	def test_skips_opening_row_lookup_prefix(self):
		columns = [{"fieldname": "item_code"}, {"fieldname": "item_name"}]
		data = [{"item_code": "'Opening'"}]

		with patch(
			"aimatic.stock_reports.stock_ledger_barcodes._barcodes_by_item",
			return_value={},
		) as mocked:
			_with_barcodes(columns, data)
			mocked.assert_called_once_with(set())
