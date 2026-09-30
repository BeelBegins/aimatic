import json
import unittest
from pathlib import Path

from aimatic.ai.api import _ALLOWED_ROLES as AI_ROLES
from aimatic.executive_dashboard.api import _ALLOWED_ROLES as CEO_ROLES
from aimatic.retail_finance_setup.api import ALLOWED_ROLES as FINANCE_ROLES
from aimatic.sales_dashboard.api import _ALLOWED_ROLES as SALES_ROLES
from aimatic.vendor_performance.api import _ALLOWED_ROLES as VENDOR_ROLES

PAGES = Path(__file__).resolve().parent


def _page_roles(folder: str) -> set[str]:
	payload = json.loads((PAGES / folder / f"{folder}.json").read_text(encoding="utf-8"))
	return {row["role"] for row in payload.get("roles") or []}


class TestDashboardPageRoles(unittest.TestCase):
	def test_ceo_page_roles_match_api(self):
		self.assertEqual(_page_roles("ceo_dashboard_console"), set(CEO_ROLES))

	def test_sales_page_roles_match_api(self):
		self.assertEqual(_page_roles("sales_dashboard_console"), set(SALES_ROLES))

	def test_help_stays_open(self):
		self.assertEqual(_page_roles("help_console"), set())

	def test_ai_page_roles_match_api(self):
		self.assertEqual(_page_roles("ai_assistant_console"), set(AI_ROLES))

	def test_finance_setup_page_roles_match_api(self):
		self.assertEqual(_page_roles("retail_finance_setup_console"), set(FINANCE_ROLES))

	def test_vendor_console_page_roles_match_api(self):
		self.assertEqual(_page_roles("vendor_performance_console"), set(VENDOR_ROLES))
		self.assertEqual(_page_roles("vendor_stock_positions_console"), set(VENDOR_ROLES))
