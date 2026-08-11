import json
import unittest
from unittest.mock import patch

from aimatic.purchase_barcode_import.api import (
	_normalize_barcode_list,
	_resolve_one,
	resolve_barcodes_for_purchase,
)


class TestPurchaseBarcodeImport(unittest.TestCase):
	def test_normalize_strips_and_dedupes(self):
		self.assertEqual(
			_normalize_barcode_list([" 111 ", "111", "", "222", None]),
			["111", "222"],
		)
		self.assertEqual(_normalize_barcode_list(json.dumps(["a", "a", "b"])), ["a", "b"])

	def test_resolve_one_uses_variants_map(self):
		parents = {"0671866150071": ["ITEM-A"], "671866150071": ["ITEM-A"]}
		self.assertEqual(_resolve_one("0671866150071", parents), ["ITEM-A"])
		self.assertEqual(_resolve_one("671866150071", parents), ["ITEM-A"])

	def test_resolve_one_collects_ambiguous_parents(self):
		parents = {"111": ["ITEM-A", "ITEM-B"]}
		self.assertEqual(_resolve_one("111", parents), ["ITEM-A", "ITEM-B"])

	@patch("aimatic.purchase_barcode_import.api.frappe")
	def test_resolve_barcodes_for_purchase_partitions(self, frappe):
		frappe.has_permission.return_value = True
		frappe.db.sql.return_value = [
			{"barcode": "111", "parent": "ITEM-OK"},
			{"barcode": "222", "parent": "ITEM-A"},
			{"barcode": "222", "parent": "ITEM-B"},
			{"barcode": "333", "parent": "ITEM-OFF"},
		]
		frappe.db.exists.return_value = False

		def cached_value(doctype, name, field):
			return 1 if name == "ITEM-OFF" else 0

		frappe.get_cached_value.side_effect = cached_value

		result = resolve_barcodes_for_purchase(["111", "222", "333", "999"])

		self.assertEqual(result["matched"], {"111": "ITEM-OK"})
		self.assertEqual(result["unmatched"], ["999"])
		self.assertEqual(
			result["ambiguous"],
			[{"barcode": "222", "item_codes": ["ITEM-A", "ITEM-B"]}],
		)
		self.assertEqual(
			result["disabled"],
			[{"barcode": "333", "item_code": "ITEM-OFF"}],
		)


if __name__ == "__main__":
	unittest.main()
