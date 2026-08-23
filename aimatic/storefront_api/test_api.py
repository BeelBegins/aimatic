"""
Tests for aimatic.storefront_api.api.

Run with:
    bench --site <site> run-tests --app aimatic --module aimatic.storefront_api.test_api
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.exceptions import PermissionError as FrappePermissionError


class _AimTestCase(unittest.TestCase):
	"""Saves/restores the Frappe session user and rolls back the DB after each test."""

	def setUp(self):
		self._original_user = frappe.session.user
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user(self._original_user)
		frappe.db.rollback()


class TestStorefrontRoleGate(_AimTestCase):
	def test_get_branches_denied_without_role(self):
		from aimatic.storefront_api.api import get_branches

		with patch("aimatic.storefront_api.utils.frappe.get_roles", return_value=["Sales User"]):
			with self.assertRaises(FrappePermissionError):
				get_branches()

	def test_get_sync_status_allowed_with_role(self):
		from aimatic.storefront_api.api import get_sync_status

		with patch(
			"aimatic.storefront_api.utils.frappe.get_roles",
			return_value=["Storefront Integration"],
		):
			result = get_sync_status()
		self.assertIn("max_modified", result)
		self.assertIn("Item", result["max_modified"])

	def test_get_deleted_items_denied_without_role(self):
		from aimatic.storefront_api.api import get_deleted_items

		with patch("aimatic.storefront_api.utils.frappe.get_roles", return_value=[]):
			with self.assertRaises(FrappePermissionError):
				get_deleted_items(since="2026-01-01")


class TestGetItems(_AimTestCase):
	def test_returns_barcodes_nested_per_item(self):
		from aimatic.storefront_api.api import get_items

		item_code = frappe.db.get_value("Item", {"disabled": 0, "is_sales_item": 1}, "name")
		if not item_code:
			self.skipTest("No active sales item on this site to test against")

		with patch(
			"aimatic.storefront_api.utils.frappe.get_roles",
			return_value=["Storefront Integration"],
		):
			result = get_items(limit_page_length=5)

		self.assertIn("rows", result)
		self.assertIn("has_more", result)
		for row in result["rows"]:
			self.assertIn("barcodes", row)
			self.assertIsInstance(row["barcodes"], list)


class TestResolveBranchPriceList(_AimTestCase):
	def test_unknown_branch_raises(self):
		from aimatic.storefront_api.utils import resolve_branch_price_list

		with self.assertRaises(frappe.DoesNotExistError):
			resolve_branch_price_list("Definitely Not A Real Branch")
