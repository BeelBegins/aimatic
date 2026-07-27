def before_tests():
	"""Load ERPNext's standard test master data before Aimatic integration tests."""
	import frappe
	from erpnext.tests import utils as erpnext_test_utils

	from aimatic.patches.create_shopping_oauth_client import execute as create_shopping_oauth_client
	from aimatic.setup import setup_pos_master_data_permissions

	# Importing erpnext.tests.utils normally creates this data as a module side
	# effect. Test discovery can import the module before this hook and later
	# roll back that transaction, however, leaving the module cached but the
	# records absent. Check the records themselves and rebuild them when needed.
	test_masters_available = all((
		frappe.db.exists("Territory", {"is_group": 0}),
		frappe.db.exists("Item Group", {"is_group": 0}),
		frappe.db.exists("UOM", "Nos"),
		frappe.db.exists("Price List", {"selling": 1, "enabled": 1}),
		frappe.db.get_single_value("Selling Settings", "selling_price_list"),
	))
	if not test_masters_available:
		erpnext_test_utils.BootStrapTestData()

	setup_pos_master_data_permissions()
	create_shopping_oauth_client()
