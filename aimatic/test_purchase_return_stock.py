import unittest
from types import SimpleNamespace

from aimatic.purchase_return_stock import ensure_purchase_invoice_return_updates_stock


class TestPurchaseReturnStock(unittest.TestCase):
	def test_sets_update_stock_on_draft_return(self):
		doc = SimpleNamespace(
			doctype="Purchase Invoice",
			docstatus=0,
			is_return=1,
			update_stock=0,
		)
		ensure_purchase_invoice_return_updates_stock(doc)
		self.assertEqual(doc.update_stock, 1)

	def test_leaves_normal_invoice_alone(self):
		doc = SimpleNamespace(
			doctype="Purchase Invoice",
			docstatus=0,
			is_return=0,
			update_stock=0,
		)
		ensure_purchase_invoice_return_updates_stock(doc)
		self.assertEqual(doc.update_stock, 0)

	def test_skips_submitted_documents(self):
		doc = SimpleNamespace(
			doctype="Purchase Invoice",
			docstatus=1,
			is_return=1,
			update_stock=0,
		)
		ensure_purchase_invoice_return_updates_stock(doc)
		self.assertEqual(doc.update_stock, 0)
