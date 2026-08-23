import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.shopping import api


class TestShoppingApi(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.original_user = frappe.session.user
		suffix = uuid.uuid4().hex[:10]
		self.customer_group = (
			frappe.get_doc(
				{
					"doctype": "Customer Group",
					"customer_group_name": f"Shopping Test {suffix}",
					"parent_customer_group": "All Customer Groups",
					"is_group": 0,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		self.territory = (
			frappe.get_doc(
				{
					"doctype": "Territory",
					"territory_name": f"Shopping Test {suffix}",
					"parent_territory": "All Territories",
					"is_group": 0,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.set_single_value("Shopping Settings", "enabled", 1)
		frappe.db.set_single_value("Shopping Settings", "allow_self_registration", 1)
		frappe.db.set_single_value("Shopping Settings", "registration_customer_group", self.customer_group)
		frappe.db.set_single_value("Shopping Settings", "registration_territory", self.territory)
		self.email = f"shopping-test-{uuid.uuid4().hex[:10]}@example.invalid"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": self.email,
				"first_name": "Shopping Test",
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user(self.original_user)
		super().tearDown()

	def test_native_public_config_exposes_no_secret(self):
		config = api.get_public_config()
		self.assertTrue(config["oauth_client_id"])
		self.assertEqual(config["redirect_uri"], "com.beelbegins.aimaticshopping://oauth/callback")
		self.assertNotIn("client_secret", config)

	def test_website_user_registration_is_retry_safe(self):
		frappe.set_user(self.email)
		created = api.register_customer("Shopping Test Customer", "+92 300 0000000")
		retried = api.register_customer("Ignored Retry Name")

		self.assertTrue(created["created"])
		self.assertFalse(retried["created"])
		self.assertEqual(created["customer"], retried["customer"])
		self.assertEqual(api.get_customer_account()["customer"], created["customer"])
		self.assertEqual(frappe.db.get_value("Customer", created["customer"], "email_id"), self.email)

	def test_registration_does_not_claim_existing_customer_by_email(self):
		unrelated = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Unrelated {uuid.uuid4().hex[:8]}",
				"customer_type": "Individual",
				"customer_group": self.customer_group,
				"territory": self.territory,
				"email_id": self.email,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.email)
		created = api.register_customer(f"New {uuid.uuid4().hex[:8]}")

		self.assertNotEqual(created["customer"], unrelated.name)
