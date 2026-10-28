import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from redis.exceptions import LockError

from aimatic.insights_workbooks import pos_margin_cache as cache


class TestMarginCacheTransaction(unittest.TestCase):
	def setUp(self):
		self.events = []
		self.frappe = MagicMock()
		self.frappe.db.exists.return_value = True
		self.frappe.db.commit.side_effect = lambda: self.events.append("commit")
		self.frappe.db.rollback.side_effect = lambda: self.events.append("rollback")

		@contextmanager
		def lock(*args, **kwargs):
			self.events.append("lock")
			try:
				yield
			finally:
				self.events.append("unlock")

		self.frappe.cache.lock.side_effect = lock

	def run_refresh(self, names, rows):
		with (
			patch.object(cache, "frappe", self.frappe),
			patch.object(cache, "get_missing_invoices", return_value=names),
			patch.object(cache, "get_margin_rows", return_value=rows),
		):
			return cache.refresh_missing()

	def test_success_commits_before_releasing_lock(self):
		row = SimpleNamespace(pos_invoice="POS-1")
		# Frappe rows support both attribute and mapping access.
		import frappe

		row = frappe._dict(pos_invoice=row.pos_invoice, posting_date="2026-10-01", sales=100)
		self.assertEqual(self.run_refresh(["POS-1"], [row]), {"upserted": 1})
		self.assertEqual(self.events, ["lock", "commit", "unlock"])
		self.assertEqual(row.name, "POS-1")
		self.assertEqual(row.doctype, "Insights POS Margin Daily")
		self.frappe.get_doc.return_value.insert.assert_called_once_with(ignore_permissions=True)

	def test_insert_failure_rolls_back_before_releasing_lock(self):
		import frappe

		row = frappe._dict(pos_invoice="POS-1")
		self.frappe.get_doc.return_value.insert.side_effect = RuntimeError("insert failed")
		with self.assertRaisesRegex(RuntimeError, "insert failed"):
			self.run_refresh(["POS-1"], [row])
		self.assertEqual(self.events, ["lock", "rollback", "unlock"])
		self.frappe.db.commit.assert_not_called()

	def test_empty_batch_does_not_write(self):
		self.assertEqual(self.run_refresh([], []), {"upserted": 0})
		self.frappe.get_doc.assert_not_called()
		self.frappe.db.commit.assert_not_called()

	def test_unmatched_candidate_is_rejected_without_inserts(self):
		self.frappe.throw.side_effect = RuntimeError("candidate mismatch")
		with self.assertRaisesRegex(RuntimeError, "candidate mismatch"):
			self.run_refresh(["POS-1"], [])
		self.frappe.get_doc.assert_not_called()

	def test_overlapping_refresh_returns_busy_without_writes(self):
		self.frappe.cache.lock.side_effect = LockError("busy")
		self.assertEqual(self.run_refresh(["POS-1"], []), {"upserted": 0, "busy": True})
		self.frappe.get_doc.assert_not_called()
