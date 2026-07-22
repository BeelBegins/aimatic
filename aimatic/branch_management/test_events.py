import unittest
from types import SimpleNamespace
from unittest.mock import patch


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


if __name__ == "__main__":
	unittest.main()
