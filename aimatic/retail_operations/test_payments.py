import json
from unittest import TestCase
from unittest.mock import patch

import frappe

from aimatic.retail_operations import payments


class PaymentAlertTest(TestCase):
	def test_card_distinguishes_draft_and_currencies_without_bank_secrets(self):
		doc = frappe._dict(
			name="PE-TEST",
			docstatus=0,
			payment_type="Pay",
			posting_date="2026-11-05",
			party="Supplier",
			party_name="Vendor",
			paid_from_account_currency="PKR",
			paid_to_account_currency="USD",
			paid_amount=28000,
			received_amount=100,
			mode_of_payment="Bank",
			owner="test@example.invalid",
			bank_account="PRIVATE-BANK-ACCOUNT",
			remarks="PRIVATE-REMARK",
			references=[],
		)
		with patch(
			"aimatic.retail_operations.payments.operations_url",
			return_value="https://example.invalid/app/retail-operations-console",
		):
			body = json.dumps(payments.payment_card(doc, frappe._dict(name="Branch 1"), "Created"))
		self.assertIn("Draft", body)
		self.assertIn("bank settlement", body)
		self.assertIn("PKR", body)
		self.assertIn("USD", body)
		self.assertNotIn("PRIVATE-", body)

	def test_disabled_alerts_do_not_write(self):
		with (
			patch(
				"aimatic.retail_operations.payments.settings",
				return_value=frappe._dict(enabled=1, discord_enabled=0),
			),
			patch.object(frappe.db, "table_exists", return_value=True),
			patch("aimatic.retail_operations.payments.write_internal") as write,
		):
			payments.payment_event(frappe._dict(doctype="Payment Entry"), "after_insert")
			write.assert_not_called()

	def test_missing_branch_does_not_block_financial_event_or_guess_route(self):
		with (
			patch(
				"aimatic.retail_operations.payments.settings",
				return_value=frappe._dict(enabled=1, discord_enabled=1),
			),
			patch.object(frappe.db, "table_exists", return_value=True),
			patch("aimatic.retail_operations.payments.route") as route,
			patch("aimatic.retail_operations.payments.write_internal") as write,
			patch.object(frappe, "log_error") as error,
		):
			payments.payment_event(
				frappe._dict(doctype="Payment Entry", name="TEST-MISSING-BRANCH"), "after_insert"
			)
			write.assert_not_called()
			route.assert_not_called()
			error.assert_called_once()

	def test_other_sites_without_install_do_not_load_settings(self):
		with (
			patch.object(frappe.db, "table_exists", return_value=False),
			patch("aimatic.retail_operations.payments.settings") as config,
		):
			payments.payment_event(frappe._dict(doctype="Payment Entry"), "after_insert")
			config.assert_not_called()
