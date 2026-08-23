"""Pure helpers for Relationship Manager (no DB)."""

import unittest

from aimatic.relationship_manager.flow import SUPPORTED_DOCTYPES, _EDGES, _status_label


class TestRelationshipManagerMeta(unittest.TestCase):
	def test_supported_covers_purchase_and_sales(self):
		for dt in (
			"Purchase Order",
			"Purchase Receipt",
			"Purchase Invoice",
			"Sales Order",
			"Delivery Note",
			"Sales Invoice",
			"Payment Entry",
		):
			self.assertIn(dt, SUPPORTED_DOCTYPES)

	def test_edges_have_required_keys(self):
		for edge in _EDGES:
			self.assertIn(edge["parent"], SUPPORTED_DOCTYPES)
			self.assertIn(edge["child"], SUPPORTED_DOCTYPES)
			self.assertIn(edge["kind"], {"item_link", "return", "payment", "lcv"})
			if edge["kind"] == "item_link":
				self.assertTrue(edge.get("child_table"))
				self.assertTrue(edge.get("field"))

	def test_status_label(self):
		self.assertEqual(_status_label(0), "Draft")
		self.assertEqual(_status_label(1), "Submitted")
		self.assertEqual(_status_label(2), "Cancelled")
		self.assertEqual(_status_label(1, 1), "Submitted · Return")


if __name__ == "__main__":
	unittest.main()
