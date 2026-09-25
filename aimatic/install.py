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

	_ensure_default_selling_price_list()
	setup_pos_master_data_permissions()
	create_shopping_oauth_client()


def _ensure_default_selling_price_list():
	"""Guarantee a selling Price List and Selling Settings default for tests.

	A partial BootStrapTestData (see before_tests) can leave the site without
	either, which breaks every test that relies on the Selling Settings
	default (e.g. Customer default_price_list validation).
	"""
	import frappe

	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	if price_list and frappe.db.exists("Price List", price_list):
		return

	price_list = frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
	if not price_list:
		currency = frappe.db.get_default("currency") or "PKR"
		if not frappe.db.exists("Currency", currency):
			frappe.get_doc({"doctype": "Currency", "currency_name": currency, "enabled": 1}).insert(
				ignore_permissions=True
			)
		price_list = (
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": "Standard Selling",
					"currency": currency,
					"selling": 1,
					"enabled": 1,
				}
			)
			.insert(ignore_permissions=True, ignore_if_duplicate=True)
			.name
		)

	frappe.db.set_single_value("Selling Settings", "selling_price_list", price_list)
	frappe.db.commit()  # nosemgrep: before_tests runs outside a request transaction
