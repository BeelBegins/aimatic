def before_tests():
	"""Load ERPNext's standard test master data before Aimatic integration tests."""
	import frappe

	from aimatic.patches.create_shopping_oauth_client import execute as create_shopping_oauth_client
	from aimatic.setup import setup_pos_master_data_permissions

	def _test_masters_available():
		return all(
			(
				frappe.db.exists("Territory", {"is_group": 0}),
				frappe.db.exists("Item Group", {"is_group": 0}),
				frappe.db.exists("UOM", "Nos"),
				frappe.db.exists("Price List", {"selling": 1, "enabled": 1}),
				frappe.db.get_single_value("Selling Settings", "selling_price_list"),
			)
		)

	# Importing erpnext.tests.utils runs BootStrapTestData() as a module side
	# effect. On current ERPNext develop that can raise LinkValidationError
	# mid-bootstrap (Product Bundle before items exist) and abort the whole
	# suite before any Aimatic test runs. Only import when masters are missing,
	# and tolerate a failed/partial bootstrap so unit tests can still proceed.
	if not _test_masters_available():
		try:
			from erpnext.tests import utils as erpnext_test_utils

			if not _test_masters_available():
				erpnext_test_utils.BootStrapTestData()
		except Exception:
			frappe.log_error(title="aimatic before_tests: ERPNext BootStrapTestData failed")

	setup_pos_master_data_permissions()
	create_shopping_oauth_client()
