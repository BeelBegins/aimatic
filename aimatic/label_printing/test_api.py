import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class TestChangedPriceItemDeduplication(unittest.TestCase):
    def test_keeps_one_row_per_sku(self):
        from aimatic.label_printing.doctype.aim_label_print_job.aim_label_print_job import (
            deduplicate_changed_price_items,
        )

        rows = [
            SimpleNamespace(item_code="ITEM-1"),
            SimpleNamespace(item_code="ITEM-1"),
            SimpleNamespace(item_code="ITEM-2"),
        ]

        result = deduplicate_changed_price_items(rows)

        self.assertEqual([row.item_code for row in result], ["ITEM-1", "ITEM-2"])


class TestChangedPriceLabelRows(unittest.TestCase):
    @patch("aimatic.label_printing.api.build_item_row")
    @patch("aimatic.label_printing.api._validate_changed_price_window")
    @patch("aimatic.label_printing.api.frappe")
    def test_rate_changes_are_branch_scoped_and_deduplicated(
        self, frappe, validate_window, build_item_row
    ):
        from aimatic.label_printing.api import _get_changed_price_label_rows

        validate_window.return_value = (
            "S1 - Branch",
            date(2026, 10, 1),
            date(2026, 10, 5),
            "S1 Selling Price List",
        )
        frappe.get_all.return_value = [
            SimpleNamespace(
                item_code="ITEM-2",
                old_value=200,
                new_value=220,
                update_datetime="2026-10-04 10:00:00",
            ),
            SimpleNamespace(
                item_code="ITEM-1",
                old_value=100,
                new_value=110,
                update_datetime="2026-10-03 10:00:00",
            ),
            SimpleNamespace(
                item_code="ITEM-1",
                old_value=90,
                new_value=100,
                update_datetime="2026-10-01 09:00:00",
            ),
        ]
        build_item_row.side_effect = lambda item_code, **kwargs: {
            "item_code": item_code,
            "item_name": item_code,
        }

        result = _get_changed_price_label_rows("S1 - Branch", "2026-10-01", "2026-10-05")

        self.assertEqual([row["item_code"] for row in result["items"]], ["ITEM-1", "ITEM-2"])
        self.assertEqual(result["changed_item_count"], 2)
        self.assertEqual(build_item_row.call_count, 2)
        self.assertEqual(build_item_row.call_args.kwargs["price_list"], "S1 Selling Price List")
        filters = frappe.get_all.call_args.kwargs["filters"]
        self.assertEqual(filters["field_updated"], "Rate")
        self.assertEqual(filters["branch"], "S1 - Branch")
        self.assertEqual(filters["price_list"], "S1 Selling Price List")

        _get_changed_price_label_rows(
            "S1 - Branch", "2026-10-01", "2026-10-05", label_type="Barcode Label"
        )
        self.assertEqual(build_item_row.call_args.kwargs["label_type"], "Barcode Label")


class TestLabelPrintJobValidation(unittest.TestCase):
    def test_manual_source_does_not_validate_changed_price_dates(self):
        from aimatic.label_printing.doctype.aim_label_print_job.aim_label_print_job import (
            AIMLabelPrintJob,
        )

        doc = SimpleNamespace(source_type="Manual", replacement_status=None)

        AIMLabelPrintJob.validate_changed_price_source(doc)

        self.assertEqual(doc.replacement_status, "Not Applicable")


class TestChangedPriceFieldDependencies(unittest.TestCase):
    def test_branch_and_dates_are_only_mandatory_for_changed_prices(self):
        path = (
            Path(__file__).parent
            / "doctype"
            / "aim_label_print_job"
            / "aim_label_print_job.json"
        )
        schema = json.loads(path.read_text())
        fields = {field["fieldname"]: field for field in schema["fields"]}

        for fieldname in ("branch", "price_change_from_date", "price_change_to_date"):
            field = fields[fieldname]
            self.assertNotIn("reqd", field)
            self.assertEqual(
                field["mandatory_depends_on"],
                "eval:doc.source_type=='Changed Selling Prices'",
            )


if __name__ == "__main__":
    unittest.main()
