import unittest

from aimatic.retail_finance_setup.registry import REGISTRY_VERSION, get_capabilities


class TestRetailFinanceCapabilityRegistry(unittest.TestCase):
	def test_registry_has_unique_complete_records(self):
		capabilities = get_capabilities()
		ids = [row["id"] for row in capabilities]
		self.assertEqual(len(ids), len(set(ids)))
		self.assertGreaterEqual(len(ids), 30)
		self.assertRegex(REGISTRY_VERSION, r"^\d+\.\d+\.\d+$")
		for row in capabilities:
			self.assertTrue({"id", "label", "version", "category", "phase", "implementation_status", "description", "guidance"}.issubset(row))
			self.assertIn(row["implementation_status"], {"implemented", "standard", "partial", "missing", "separate"})

	def test_critical_finance_controls_cannot_disappear(self):
		by_id = {row["id"]: row for row in get_capabilities()}
		for capability_id in ("company_foundation", "store_accounting_dimension", "pos_cashier_controls"):
			self.assertIn(capability_id, by_id)
			self.assertTrue(by_id[capability_id]["critical"])

	def test_known_separate_backlog_remains_registered(self):
		by_id = {row["id"]: row for row in get_capabilities()}
		for capability_id in (
			"store_balance_sheet",
			"daily_sales_deposit_reconciliation",
			"petty_cash",
			"head_office_allocation",
			"inventory_shrinkage",
			"supplier_rebates",
			"branch_ebitda",
			"stock_gl_reconciliation",
			"pos_gl_reconciliation",
			"subledger_control_reconciliation",
		):
			self.assertEqual(by_id[capability_id]["implementation_status"], "separate")

	def test_cutover_rule_is_explicit(self):
		opening = next(row for row in get_capabilities() if row["id"] == "opening_balance_cutover")
		self.assertEqual(opening["implementation_status"], "partial")
		self.assertIn("accepted cutover baseline", opening["description"])
		self.assertIn("Do not reconstruct", opening["guidance"])


if __name__ == "__main__":
	unittest.main()
