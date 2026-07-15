def before_tests():
	"""Load ERPNext's standard test master data before Aimatic integration tests."""
	import erpnext.tests.utils

	from aimatic.patches.create_shopping_oauth_client import execute as create_shopping_oauth_client

	create_shopping_oauth_client()
