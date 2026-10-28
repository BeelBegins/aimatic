import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _Row:
	def __init__(self, cost_center=None, warehouse=None):
		self.cost_center = cost_center
		self.warehouse = warehouse
		self.rejected_qty = 0
		self.rejected_warehouse = None
		self.meta = SimpleNamespace(
			has_field=lambda fieldname: fieldname in {"cost_center", "warehouse", "rejected_warehouse"}
		)

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)


class _Document:
	def __init__(self, doctype):
		self.doctype = doctype
		self.company = "Test Company"
		self.branch = None
		self.cost_center = None
		self.set_warehouse = None
		self.rejected_warehouse = None
		self.items = []
		self.meta = SimpleNamespace(has_field=lambda fieldname: fieldname == "set_warehouse")

	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)


class TestAdministratorBranchSelection(unittest.TestCase):
	@patch("aimatic.branch_management.events._apply_item_row_defaults")
	@patch("aimatic.branch_management.events.get_branch_defaults")
	@patch("aimatic.branch_management.events.get_user_default_branch", return_value=None)
	@patch("aimatic.branch_management.events.user_can_override", return_value=True)
	@patch("aimatic.branch_management.events.frappe.get_all", return_value=["Only Branch"])
	def test_single_company_branch_is_selected_automatically(
		self, _get_all, _can_override, _default_branch, get_defaults, _apply_rows
	):
		from aimatic.branch_management.events import apply_branch_defaults

		get_defaults.return_value = {
			"cost_center": "Only Branch - CC",
			"finished_goods_warehouse": "Only Branch - WH",
			"rejected_warehouse": "Only Branch Rejected - WH",
		}
		doc = _Document("Purchase Receipt")

		apply_branch_defaults(doc)

		self.assertEqual(doc.branch, "Only Branch")
		self.assertEqual(doc.cost_center, "Only Branch - CC")
		self.assertEqual(doc.set_warehouse, "Only Branch - WH")

	@patch("aimatic.branch_management.events.get_user_default_branch", return_value=None)
	@patch("aimatic.branch_management.events.user_can_override", return_value=True)
	@patch("aimatic.branch_management.events.frappe.get_all", return_value=["Branch One", "Branch Two"])
	@patch("aimatic.branch_management.events.frappe.throw", side_effect=ValueError)
	def test_multiple_company_branches_require_explicit_selection(
		self, throw, _get_all, _can_override, _default_branch
	):
		from aimatic.branch_management.events import apply_branch_defaults

		doc = _Document("Purchase Order")
		with self.assertRaises(ValueError):
			apply_branch_defaults(doc)

		message = str(throw.call_args.args[0])
		self.assertIn("multiple Branches", message)
		self.assertIn("Select a Branch", message)
		self.assertIn("Purchase Order", message)


class TestPurchaseBranchCostCenterForce(unittest.TestCase):
	@patch("aimatic.branch_management.events.get_branch_defaults")
	@patch("aimatic.branch_management.events.get_user_default_branch", return_value="S5 - Sector C")
	@patch("aimatic.branch_management.events.user_can_override", return_value=True)
	def test_override_role_replaces_company_head_office_on_purchase_items(
		self, _can_override, _default_branch, get_defaults
	):
		from aimatic.branch_management.events import apply_branch_defaults

		get_defaults.return_value = {
			"cost_center": "S5 - Sector C - SSM",
			"finished_goods_warehouse": "S5 - Sector C - SSM",
			"rejected_warehouse": "S5 Rejected - SSM",
		}
		doc = _Document("Purchase Receipt")
		doc.branch = "S5 - Sector C"
		doc.cost_center = "S5 - Sector C - SSM"
		doc.items = [
			_Row(cost_center="Head Office - SSM"),
			_Row(cost_center="Head Office - SSM"),
		]

		apply_branch_defaults(doc)

		self.assertEqual(doc.cost_center, "S5 - Sector C - SSM")
		self.assertEqual(doc.items[0].cost_center, "S5 - Sector C - SSM")
		self.assertEqual(doc.items[1].cost_center, "S5 - Sector C - SSM")

	@patch("aimatic.branch_management.events.get_branch_defaults")
	@patch("aimatic.branch_management.events.get_user_default_branch", return_value="S1 - Ghouri Town VIP")
	@patch("aimatic.branch_management.events.user_can_override", return_value=True)
	def test_override_role_replaces_head_office_header_on_purchase_order(
		self, _can_override, _default_branch, get_defaults
	):
		from aimatic.branch_management.events import apply_branch_defaults

		get_defaults.return_value = {
			"cost_center": "S1 - Ghouri Town VIP - SSM",
			"finished_goods_warehouse": "S1 - Ghouri Town VIP - SSM",
			"rejected_warehouse": None,
		}
		doc = _Document("Purchase Order")
		doc.branch = "S1 - Ghouri Town VIP"
		doc.cost_center = "Head Office - SSM"
		doc.items = [_Row(cost_center="Head Office - SSM")]

		apply_branch_defaults(doc)

		self.assertEqual(doc.cost_center, "S1 - Ghouri Town VIP - SSM")
		self.assertEqual(doc.items[0].cost_center, "S1 - Ghouri Town VIP - SSM")

	@patch("aimatic.branch_management.events.get_branch_defaults")
	@patch("aimatic.branch_management.events.get_user_default_branch", return_value="S5 - Sector C")
	@patch("aimatic.branch_management.events.user_can_override", return_value=True)
	def test_stock_entry_keeps_fill_blanks_only_for_override_roles(
		self, _can_override, _default_branch, get_defaults
	):
		from aimatic.branch_management.events import apply_branch_defaults

		get_defaults.return_value = {
			"cost_center": "S5 - Sector C - SSM",
			"finished_goods_warehouse": None,
			"rejected_warehouse": None,
		}
		doc = _Document("Stock Entry")
		doc.meta = SimpleNamespace(has_field=lambda fieldname: False)
		doc.branch = "S5 - Sector C"
		doc.cost_center = "Head Office - SSM"
		doc.items = [_Row(cost_center="Head Office - SSM")]
		doc.items[0].meta = SimpleNamespace(has_field=lambda fieldname: fieldname == "cost_center")

		apply_branch_defaults(doc)

		# Stock Entry is outside the purchase force; override path keeps HO.
		self.assertEqual(doc.cost_center, "Head Office - SSM")
		self.assertEqual(doc.items[0].cost_center, "Head Office - SSM")


if __name__ == "__main__":
	unittest.main()
