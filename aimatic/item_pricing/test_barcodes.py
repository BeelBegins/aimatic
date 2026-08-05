import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestItemPriceBarcodes(unittest.TestCase):
    @patch(
        "aimatic.item_pricing.barcodes.get_item_barcodes",
        return_value=["111", "222", "333"],
    )
    def test_item_price_gets_all_barcodes_in_one_export_field(self, _get_barcodes):
        from aimatic.item_pricing.barcodes import set_item_price_barcodes

        doc = SimpleNamespace(item_code="ITEM-001", custom_barcodes=None)

        set_item_price_barcodes(doc)

        self.assertEqual(doc.custom_barcodes, "111, 222, 333")

    @patch("aimatic.item_pricing.barcodes.format_item_barcodes", return_value="111, 222")
    @patch("aimatic.item_pricing.barcodes.frappe")
    def test_item_save_refreshes_all_matching_item_prices(self, frappe, _format_barcodes):
        from aimatic.item_pricing.barcodes import sync_item_barcodes_to_prices

        frappe.db.has_column.return_value = True
        sync_item_barcodes_to_prices(SimpleNamespace(name="ITEM-001"))

        frappe.db.set_value.assert_called_once_with(
            "Item Price",
            {"item_code": "ITEM-001"},
            "custom_barcodes",
            "111, 222",
            update_modified=False,
        )

    @patch("aimatic.item_pricing.barcodes.frappe")
    def test_item_sync_is_safe_before_custom_field_exists(self, frappe):
        from aimatic.item_pricing.barcodes import sync_item_barcodes_to_prices

        frappe.db.has_column.return_value = False
        sync_item_barcodes_to_prices(SimpleNamespace(name="ITEM-001"))

        frappe.db.set_value.assert_not_called()


if __name__ == "__main__":
    unittest.main()
