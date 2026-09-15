"""Unit tests for Principal Historical Allocation draft automation (no DB)."""

from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from aimatic.principal_allocation_automation import (
	_propose_one_voucher,
	_resolve_line_principal,
)


class TestPrincipalAllocationAutomation(unittest.TestCase):
	def test_item_mapping_wins_over_later_evidence(self):
		principal, status, _later = _resolve_line_principal(
			"ITEM-1",
			date(2026, 8, 1),
			{"ITEM-1": "ABBOTT"},
			{"ITEM-1": [SimpleNamespace(posting_date=date(2026, 9, 1), principal="SHIELD", voucher_no="PI-1")]},
		)
		self.assertEqual(principal, "ABBOTT")
		self.assertEqual(status, "Approved Item Mapping")

	def test_unique_later_evidence(self):
		principal, status, later = _resolve_line_principal(
			"ITEM-1",
			date(2026, 8, 1),
			{},
			{
				"ITEM-1": [
					SimpleNamespace(posting_date=date(2026, 7, 1), principal="OLD", voucher_no="PI-OLD"),
					SimpleNamespace(posting_date=date(2026, 9, 1), principal="ABBOTT", voucher_no="PI-1"),
					SimpleNamespace(posting_date=date(2026, 9, 5), principal="ABBOTT", voucher_no="PI-2"),
				]
			},
		)
		self.assertEqual(principal, "ABBOTT")
		self.assertEqual(status, "Unique Later Evidence")
		self.assertEqual(len(later), 2)

	def test_conflicting_later_evidence(self):
		principal, status, _later = _resolve_line_principal(
			"ITEM-1",
			date(2026, 8, 1),
			{},
			{
				"ITEM-1": [
					SimpleNamespace(posting_date=date(2026, 9, 1), principal="ABBOTT", voucher_no="PI-1"),
					SimpleNamespace(posting_date=date(2026, 9, 5), principal="SHIELD", voucher_no="PI-2"),
				]
			},
		)
		self.assertIsNone(principal)
		self.assertEqual(status, "Conflicting Later Evidence")

	def test_ready_proposal_scales_to_document_total(self):
		voucher = SimpleNamespace(
			name="PINV-1",
			posting_date=date(2026, 8, 1),
			document_total=117.0,
		)
		lines = [
			SimpleNamespace(item_code="A", line_amount=60, source_row="r1"),
			SimpleNamespace(item_code="B", line_amount=40, source_row="r2"),
		]
		proposal = _propose_one_voucher(
			voucher_type="Purchase Invoice",
			voucher=voucher,
			lines=lines,
			item_principals={"A": "ABBOTT", "B": "ABBOTT"},
			evidence={},
		)
		self.assertEqual(proposal["status"], "ready")
		self.assertEqual(len(proposal["allocations"]), 1)
		self.assertAlmostEqual(proposal["allocations"][0]["allocated_amount"], 117.0)
		self.assertIn("Optional review", proposal["evidence_notes"])

	def test_conflict_is_skipped(self):
		voucher = SimpleNamespace(name="PINV-2", posting_date=date(2026, 8, 1), document_total=100)
		lines = [SimpleNamespace(item_code="A", line_amount=100, source_row="r1")]
		proposal = _propose_one_voucher(
			voucher_type="Purchase Invoice",
			voucher=voucher,
			lines=lines,
			item_principals={},
			evidence={
				"A": [
					SimpleNamespace(posting_date=date(2026, 9, 1), principal="ABBOTT", voucher_no="X"),
					SimpleNamespace(posting_date=date(2026, 9, 2), principal="SHIELD", voucher_no="Y"),
				]
			},
		)
		self.assertEqual(proposal["status"], "skipped")
		self.assertEqual(proposal["skip_reason"], "Conflicting Later Evidence")


if __name__ == "__main__":
	unittest.main()
