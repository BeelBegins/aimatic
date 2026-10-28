"""Unit tests for Vendor Stock Positions multi-mode helpers (no DB)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe

from aimatic.vendor_performance.api import (
	_MAX_ORIGIN_STOCK_SLE_ROWS,
	_normalize_stock_basis,
	_parse_barcode_tokens,
)
from aimatic.vendor_performance.stock_positions_modes import (
	_attach_origin_fields,
	_fan_out_bin_rows,
	build_xlsx_data,
)


class TestStockBasisAndBarcodes(unittest.TestCase):
	def test_normalize_stock_basis_defaults_to_bin(self):
		self.assertEqual(_normalize_stock_basis(None), "bin")
		self.assertEqual(_normalize_stock_basis("BOTH"), "both")

	def test_normalize_stock_basis_rejects_unknown(self):
		with self.assertRaises((frappe.ValidationError, RuntimeError)):
			_normalize_stock_basis("average")

	def test_parse_barcode_tokens_dedupes_and_splits(self):
		tokens = _parse_barcode_tokens("  a\nb\ta,b\n c \n")
		self.assertEqual(tokens, ["a", "b", "c"])

	def test_parse_barcode_tokens_list_input(self):
		self.assertEqual(_parse_barcode_tokens(["x", "x", " y "]), ["x", "y"])


class TestFanOutAndXlsx(unittest.TestCase):
	def test_fan_out_bin_rows_duplicates_shared_sku(self):
		stock_rows = [{"item_code": "ITEM-1", "item_name": "One", "stock_qty": 5, "stock_value": 50}]
		links_by_item = {"ITEM-1": ["SUP-A", "SUP-B"]}
		supplier_names = {"SUP-A": "Supplier A", "SUP-B": "Supplier B"}
		rows = _fan_out_bin_rows(stock_rows, links_by_item, supplier_names, group_by_warehouse=False)
		self.assertEqual(len(rows), 2)
		self.assertEqual({row["supplier"] for row in rows}, {"SUP-A", "SUP-B"})
		self.assertTrue(all(row["stock_qty"] == 5 for row in rows))
		self.assertTrue(all("origin_stock_qty" in row for row in rows))

	def test_attach_origin_fields_from_map(self):
		row = {"item_code": "ITEM-1"}
		_attach_origin_fields(
			row,
			{"origin_stock_qty": 2, "origin_stock_value": 20, "last_origin_doc": "PR-1"},
		)
		self.assertEqual(row["origin_stock_qty"], 2)
		self.assertEqual(row["origin_stock_value"], 20)
		self.assertEqual(row["last_origin_doc"], "PR-1")

	def test_xlsx_includes_supplier_and_origin_and_unresolved(self):
		payload = {
			"mode": "by_barcodes",
			"currency": "PKR",
			"include_supplier_columns": True,
			"include_origin_columns": True,
			"group_by_warehouse": False,
			"stock_items": [
				{
					"supplier": "SUP-A",
					"supplier_name": "Supplier A",
					"item_code": "ITEM-1",
					"item_name": "One",
					"barcode1": "111",
					"barcode2": "",
					"stock_qty": 1,
					"stock_value": 10,
					"stock_value_incl_tax": 11,
					"stock_tax_amount": 1,
					"origin_stock_qty": 1,
					"origin_stock_value": 9,
					"purchase_qty_in_window": 0,
					"purchase_amount_in_window": 0,
					"purchase_tax_amount_in_window": 0,
					"purchase_amount_incl_tax_in_window": 0,
					"sales_qty_in_window": 0,
					"sales_amount_in_window": 0,
					"cogs_amount_in_window": 0,
					"gross_margin_in_window": 0,
					"adjustment_qty_in_window": 0,
					"adjustment_value_in_window": 0,
				}
			],
			"unresolved_barcodes": ["MISSING-1"],
		}
		data, filename = build_xlsx_data(payload)
		self.assertEqual(filename, "vendor_stock_positions_by_barcodes")
		self.assertIn("Supplier", data[0])
		self.assertIn("Origin Stock Qty", data[0])
		self.assertEqual(data[1][0], "SUP-A")
		self.assertIn(["Unresolved Barcodes / Item Codes"], data)
		self.assertIn(["MISSING-1"], data)


class TestFifoTooLargeShortCircuit(unittest.TestCase):
	@patch("aimatic.vendor_performance.api._count_sle_rows", return_value=_MAX_ORIGIN_STOCK_SLE_ROWS + 1)
	def test_fifo_map_returns_too_large(self, _mock_count):
		from aimatic.vendor_performance.api import _get_fifo_origin_by_supplier_item

		too_large, sle_count, origin_map = _get_fifo_origin_by_supplier_item(
			company="Test Company",
			item_codes=["ITEM-1"],
			warehouse_list=None,
		)
		self.assertTrue(too_large)
		self.assertEqual(sle_count, _MAX_ORIGIN_STOCK_SLE_ROWS + 1)
		self.assertEqual(origin_map, {})


if __name__ == "__main__":
	unittest.main()
