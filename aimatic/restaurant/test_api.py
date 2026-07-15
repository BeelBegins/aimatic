from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.restaurant.api import _table_status, _validate_modifiers


class TestRestaurantApi(FrappeTestCase):
	def test_table_status_never_depends_on_colour(self):
		self.assertEqual(_table_status(None), "Available")
		self.assertEqual(_table_status(SimpleNamespace(status="Open", items=[])), "Occupied")
		self.assertEqual(
			_table_status(SimpleNamespace(status="Bill Requested", items=[])),
			"Bill requested",
		)
		ready = SimpleNamespace(qty=1, sent_qty=1, kitchen_status="Ready")
		self.assertEqual(_table_status(SimpleNamespace(status="Sent to Kitchen", items=[ready])), "Needs attention")

	@patch("aimatic.restaurant.api._modifier_configuration")
	def test_modifier_prices_are_server_authoritative(self, configuration):
		configuration.return_value = [{
			"code": "Size",
			"title": "Size",
			"required": True,
			"multiple": False,
			"minimum": 1,
			"maximum": 1,
			"options": [{"code": "Size:large", "label": "Large", "price": 250, "linked_item": None}],
		}]
		snapshot, adjustment = _validate_modifiers("PIZZA", [{"code": "Size:large", "price": 1}])
		self.assertEqual(adjustment, 250)
		self.assertEqual(snapshot[0]["label"], "Large")

	@patch("aimatic.restaurant.api._modifier_configuration", return_value=[])
	def test_unknown_modifier_is_rejected(self, _configuration):
		with self.assertRaises(frappe.ValidationError):
			_validate_modifiers("ITEM", [{"code": "forged-option"}])
