import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aimatic.journal_entry_cost_center import sync_journal_entry_cost_centers


class TestJournalEntryCostCenter(unittest.TestCase):
	@patch("aimatic.journal_entry_cost_center._account_report_type")
	@patch("aimatic.journal_entry_cost_center._branch_cost_center")
	@patch("aimatic.journal_entry_cost_center._company_default_cost_center")
	def test_syncs_from_branch_when_company_default(
		self, company_cc, branch_cc, report_type
	):
		company_cc.return_value = "Head Office - SSM"
		branch_cc.return_value = "S1 - Ghouri Town VIP - SSM"
		report_type.return_value = "Profit and Loss"
		row = SimpleNamespace(
			idx=1,
			account="Travel - SSM",
			branch="S1 - Ghouri Town VIP",
			cost_center="Head Office - SSM",
			get=lambda k, d=None: getattr(row, k, d),
		)
		doc = SimpleNamespace(
			doctype="Journal Entry",
			docstatus=0,
			company="Siezal Supermarket",
			accounts=[row],
			get=lambda k, d=None: getattr(doc, k, d),
		)

		sync_journal_entry_cost_centers(doc)
		self.assertEqual(row.cost_center, "S1 - Ghouri Town VIP - SSM")

	@patch("aimatic.journal_entry_cost_center.frappe.throw")
	@patch("aimatic.journal_entry_cost_center._account_report_type")
	@patch("aimatic.journal_entry_cost_center._branch_cost_center")
	@patch("aimatic.journal_entry_cost_center._company_default_cost_center")
	def test_throws_when_pnl_missing_cost_center(
		self, company_cc, branch_cc, report_type, throw
	):
		company_cc.return_value = "Head Office - SSM"
		branch_cc.return_value = None
		report_type.return_value = "Profit and Loss"
		row = SimpleNamespace(
			idx=2,
			account="Travel - SSM",
			branch=None,
			cost_center=None,
			get=lambda k, d=None: getattr(row, k, d),
		)
		doc = SimpleNamespace(
			doctype="Journal Entry",
			docstatus=0,
			company="Siezal Supermarket",
			accounts=[row],
			get=lambda k, d=None: getattr(doc, k, d),
		)

		sync_journal_entry_cost_centers(doc)
		throw.assert_called_once()
