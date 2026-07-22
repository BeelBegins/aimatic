import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


class TestBranchPriceListInitialization(unittest.TestCase):
	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_created_price_list_is_selling_only_and_copies_baseline(self, frappe):
		from aimatic.shelf_pricing.utils import get_or_create_branch_price_list

		frappe.db.get_value.return_value = None
		frappe.db.exists.return_value = False
		frappe.db.get_single_value.side_effect = ["PKR", "Standard Selling"]
		frappe.session.user = "Administrator"
		frappe.generate_hash.return_value = "item-price-hash"
		baseline = {
			"item_code": "ITEM-1",
			"uom": "Pcs",
			"packing_unit": 0,
			"item_name": "Item One",
			"brand": None,
			"item_description": None,
			"customer": None,
			"batch_no": None,
			"currency": "PKR",
			"price_list_rate": 125,
			"custom_latest_price_incl_taxes": 0,
			"custom_mrp": 0,
			"valid_from": None,
			"lead_time_days": 0,
			"valid_upto": None,
			"note": None,
			"reference": None,
		}
		frappe.get_all.side_effect = [[baseline], ["POS-1"]]
		inserted = []

		def get_doc(values):
			inserted.append(values)
			return Mock()

		frappe.get_doc.side_effect = get_doc
		result = get_or_create_branch_price_list("Branch One")

		self.assertEqual(result, "Branch One Selling Price List")
		self.assertEqual(inserted[0]["doctype"], "Price List")
		self.assertEqual(inserted[0]["selling"], 1)
		self.assertEqual(inserted[0]["buying"], 0)
		frappe.db.bulk_insert.assert_called_once()
		bulk_args = frappe.db.bulk_insert.call_args.args
		self.assertEqual(bulk_args[0], "Item Price")
		fields, values = bulk_args[1], bulk_args[2]
		self.assertEqual(values[0][fields.index("price_list")], "Branch One Selling Price List")
		self.assertEqual(values[0][fields.index("buying")], 0)
		self.assertEqual(values[0][fields.index("selling")], 1)
		self.assertIn(call("Branch", "Branch One", "default_selling_price_list", "Branch One Selling Price List"), frappe.db.set_value.call_args_list)
		self.assertIn(call("POS Profile", "POS-1", "selling_price_list", "Branch One Selling Price List"), frappe.db.set_value.call_args_list)

	@patch("aimatic.shelf_pricing.utils.frappe")
	def test_existing_branch_list_must_be_enabled_and_selling_only(self, frappe):
		from aimatic.shelf_pricing.utils import get_or_create_branch_price_list

		frappe.db.get_value.side_effect = [
			"Wrong List",
			SimpleNamespace(selling=1, buying=1, enabled=1),
		]
		frappe.throw.side_effect = ValueError

		with self.assertRaises(ValueError):
			get_or_create_branch_price_list("Branch One")

	@patch("aimatic.shelf_pricing.utils.get_or_create_branch_price_list", return_value="Branch One Selling Price List")
	def test_new_branch_event_initializes_price_list(self, initialize):
		from aimatic.branch_management.events import initialize_branch_selling_price_list

		doc = SimpleNamespace(name="Branch One", default_selling_price_list=None)
		initialize_branch_selling_price_list(doc)
		initialize.assert_called_once_with("Branch One")
		self.assertEqual(doc.default_selling_price_list, "Branch One Selling Price List")

	@patch("aimatic.shelf_pricing.utils.get_or_create_branch_price_list", return_value="Branch One Selling Price List")
	def test_pos_profile_always_uses_its_branch_price_list(self, initialize):
		from aimatic.branch_management.events import apply_pos_profile_branch_price_list

		doc = SimpleNamespace(branch="Branch One", selling_price_list="Standard Selling")
		apply_pos_profile_branch_price_list(doc)
		initialize.assert_called_once_with("Branch One")
		self.assertEqual(doc.selling_price_list, "Branch One Selling Price List")

	@patch("aimatic.shelf_pricing.utils.get_or_create_branch_price_list")
	@patch("aimatic.retail_finance_setup.api._resolve_company", return_value="Test Company")
	@patch("aimatic.retail_finance_setup.api.frappe.get_roles", return_value=["Accounts Manager"])
	@patch("aimatic.retail_finance_setup.api.frappe.get_all")
	def test_setup_action_initializes_only_missing_branch_links(
		self, get_all, _roles, _resolve_company, initialize
	):
		from aimatic.retail_finance_setup.api import initialize_branch_selling_price_lists

		get_all.return_value = [
			SimpleNamespace(name="Branch One", default_selling_price_list=None),
			SimpleNamespace(name="Branch Two", default_selling_price_list="Branch Two Selling Price List"),
		]
		initialize.side_effect = ["Branch One Selling Price List", "Branch Two Selling Price List"]

		result = initialize_branch_selling_price_lists("Test Company")

		self.assertEqual(result["branch_count"], 2)
		self.assertEqual(result["initialized_count"], 1)
		self.assertEqual(result["already_configured_count"], 1)
		self.assertEqual(initialize.call_args_list, [call("Branch One"), call("Branch Two")])


if __name__ == "__main__":
	unittest.main()
