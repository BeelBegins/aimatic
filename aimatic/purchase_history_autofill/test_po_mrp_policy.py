"""PO history must never supply MRP, including to older browser clients."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aimatic.purchase_history_autofill import api, events, utils


class TestPOMRPPolicy(unittest.TestCase):
    def setUp(self):
        fields = [
            SimpleNamespace(fieldname='custom_mrp', fieldtype='Currency'),
            SimpleNamespace(fieldname='custom_gst_per', fieldtype='Percent'),
        ]
        self.meta_patch = patch.object(utils.frappe, 'get_meta', return_value=SimpleNamespace(fields=fields))
        self.meta_patch.start()
        self.addCleanup(self.meta_patch.stop)

    @staticmethod
    def history(supplier, branch, item_codes, fields):
        values = {'custom_mrp': 120, 'custom_gst_per': 18}
        return {'ITEM': {field: values[field] for field in fields}}

    def test_po_fields_exclude_mrp(self):
        self.assertEqual(utils.get_autofillable_fields('Purchase Order Item'), {'custom_gst_per': 'Percent'})

    def test_receipt_and_invoice_mrp_remain_available(self):
        for doctype in ('Purchase Receipt Item', 'Purchase Invoice Item'):
            with self.subTest(doctype=doctype):
                self.assertIn('custom_mrp', utils.get_autofillable_fields(doctype))

    def test_old_client_batch_preview_contains_no_mrp(self):
        with patch.object(api, 'fetch_latest_history_rows', side_effect=self.history):
            result = api.preview_items_history('Supplier', 'S1', ['ITEM'], 'Purchase Order Item')
        self.assertNotIn('custom_mrp', result['fieldtypes'])
        self.assertEqual(result['history']['ITEM'], {'custom_gst_per': 18})

    def test_old_client_single_preview_contains_no_mrp(self):
        with patch.object(api, 'fetch_latest_history_rows', side_effect=self.history):
            result = api.preview_item_history('Supplier', 'S1', 'ITEM', 'Purchase Order Item')
        self.assertEqual(result, {'custom_gst_per': 18})

    def test_po_save_preserves_zero_and_explicit_mrp(self):
        class Row:
            item_code = 'ITEM'

            def __init__(self, mrp):
                self.values = {'custom_mrp': mrp, 'custom_gst_per': 0}

            def get(self, key):
                return self.values.get(key)

            def set(self, key, value):
                self.values[key] = value

        for mrp in (0, 99):
            with self.subTest(mrp=mrp):
                row = Row(mrp)
                doc = SimpleNamespace(items=[row], get=lambda key: {'supplier': 'Supplier', 'branch': 'S1'}.get(key))
                with patch.object(events, 'fetch_latest_history_rows', side_effect=self.history):
                    events.autofill_purchase_order_item_fields(doc)
                self.assertEqual(row.get('custom_mrp'), mrp)
                self.assertEqual(row.get('custom_gst_per'), 18)
