from unittest import TestCase

from frappe import _dict

from aimatic.retail_operations.data import demand_window


class DemandWindowTest(TestCase):
	def window(self, configured=None, history=90):
		return demand_window(_dict(custom_demand_window_days=configured), _dict(history_days=history))

	def test_unset_branch_keeps_the_published_28_days(self):
		self.assertEqual(self.window(None), 28)
		self.assertEqual(self.window(0), 28)

	def test_branch_can_use_ninety_days(self):
		self.assertEqual(self.window(90), 90)

	def test_window_never_exceeds_fetched_history(self):
		self.assertEqual(self.window(90, history=60), 60)
		self.assertEqual(self.window(28, history=14), 14)

	def test_window_is_bounded(self):
		self.assertEqual(self.window(3), 7)
		self.assertEqual(self.window(365), 90)
