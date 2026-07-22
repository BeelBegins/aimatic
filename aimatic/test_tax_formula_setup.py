from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from aimatic import tax_formula_setup


class TestTaxFormulaSetup(TestCase):
    def test_repairs_only_missing_or_invalid_gst_links(self):
        company = SimpleNamespace(name="Siezal Super Market", abbr="SSM")
        formulas = [
            SimpleNamespace(name="GST Standard", gst_account="Sales Tax on Goods - ST"),
            SimpleNamespace(name="GST Exempted", gst_account="GST - SSM"),
            SimpleNamespace(name="GST Blank", gst_account=None),
        ]

        def get_all(doctype, **kwargs):
            return [company] if doctype == "Company" else formulas

        db = MagicMock()
        db.get_value.return_value = "GST - SSM"
        db.exists.side_effect = lambda _doctype, filters: (
            "GST - SSM" if filters["name"] == "GST - SSM" else None
        )

        with patch.object(tax_formula_setup.frappe, "get_all", side_effect=get_all), patch.object(
            tax_formula_setup.frappe, "db", db
        ):
            result = tax_formula_setup.repair_dangling_gst_accounts()

        self.assertEqual(result["gst_account"], "GST - SSM")
        self.assertEqual(result["updated"], ["GST Standard", "GST Blank"])
        self.assertEqual(
            db.set_value.call_args_list,
            [
                call(
                    "Tax Formula",
                    "GST Standard",
                    "gst_account",
                    "GST - SSM",
                    update_modified=False,
                ),
                call(
                    "Tax Formula",
                    "GST Blank",
                    "gst_account",
                    "GST - SSM",
                    update_modified=False,
                ),
            ],
        )

    def test_skips_ambiguous_multi_company_site(self):
        companies = [
            SimpleNamespace(name="Company One", abbr="ONE"),
            SimpleNamespace(name="Company Two", abbr="TWO"),
        ]
        db = MagicMock()

        with patch.object(tax_formula_setup.frappe, "get_all", return_value=companies), patch.object(
            tax_formula_setup.frappe, "db", db
        ):
            result = tax_formula_setup.repair_dangling_gst_accounts()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "site_does_not_have_exactly_one_company")
        db.get_value.assert_not_called()
        db.set_value.assert_not_called()
