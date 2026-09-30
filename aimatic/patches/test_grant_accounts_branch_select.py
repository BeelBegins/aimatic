import unittest

from aimatic.patches.grant_accounts_branch_select import ROLES


class TestGrantAccountsBranchSelect(unittest.TestCase):
	def test_roles_cover_vendor_performance_accounts_users(self):
		self.assertIn("Accounts User", ROLES)
		self.assertIn("Accounts Manager", ROLES)
		self.assertIn("Purchase Master Manager", ROLES)
