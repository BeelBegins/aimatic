import json
from datetime import datetime
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
import requests

from aimatic.retail_operations.calculations import fingerprint
from aimatic.retail_operations.discord import deliver


class DeliveryTest(TestCase):
	def run_case(self, reply=None, error=None, **changes):
		address = "https://discord.com/api/webhooks/123/example-placeholder"
		doc = frappe._dict(
			status="Pending",
			attempts=0,
			next_attempt=None,
			route_branch="B",
			channel="Purchase",
			branches='["B"]',
			destination_hash=fingerprint(address),
			payload=json.dumps({"content": "test"}),
			discord_message_id=None,
		)
		doc.update(changes)
		client = Mock()
		if error:
			client.post.side_effect = error
		else:
			client.post.return_value = reply
			client.patch.return_value = reply
		with (
			patch(
				"aimatic.retail_operations.discord.settings",
				return_value=frappe._dict(enabled=1, discord_enabled=1),
			),
			patch("aimatic.retail_operations.discord.route", return_value=address),
			patch("aimatic.retail_operations.discord.write_internal"),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe.db, "sql", return_value=[(1,)]),
			patch.object(frappe.db, "get_value", return_value=1),
			patch.object(frappe.db, "commit"),
		):
			status = deliver("delivery", client=client)
		return status, doc, client

	def test_success_records_message_id(self):
		result, doc, client = self.run_case(Mock(status_code=200, json=lambda: {"id": "999"}))
		self.assertEqual(result, "Sent")
		self.assertEqual(doc.discord_message_id, "999")
		self.assertFalse(client.post.call_args.kwargs["allow_redirects"])
		self.assertNotIn("example-placeholder", doc.payload)

	def test_timeout_is_not_blindly_retried(self):
		status, doc, client = self.run_case(error=requests.Timeout())
		self.assertEqual(status, "Uncertain")
		self.assertNotIn("https", doc.error_code)

	def test_rate_limit_respects_retry_after(self):
		status, doc, client = self.run_case(Mock(status_code=429, json=lambda: {"retry_after": 120}))
		self.assertEqual(status, "Retry")
		self.assertIsNotNone(doc.next_attempt)

	def test_sent_record_does_not_send_again(self):
		status, doc, client = self.run_case(status="Sent")
		self.assertEqual(status, "Sent")
		client.post.assert_not_called()

	def test_revision_edits_existing_message(self):
		status, doc, client = self.run_case(
			Mock(status_code=200, json=lambda: {"id": "999"}), discord_message_id="999"
		)
		self.assertEqual(status, "Sent")
		client.patch.assert_called_once()
		client.post.assert_not_called()

	def test_bad_destination_fails_without_exposing_response(self):
		status, doc, client = self.run_case(Mock(status_code=404, text="private provider body"))
		self.assertEqual(status, "Failed")
		self.assertNotIn("private", doc.error_code)

	def test_disabled_delivery_seeds_old_issue_baseline(self):
		from aimatic.retail_operations.discord import queue_urgent

		with (
			patch("aimatic.retail_operations.discord.settings", return_value=frappe._dict(discord_enabled=0)),
			patch.object(frappe.db, "sql") as update,
			patch("aimatic.retail_operations.discord.now_datetime", return_value=datetime(2026, 11, 5)),
		):
			self.assertEqual(queue_urgent({"branch": "B"}, baseline=False), [])
			self.assertIn("notified_hash=signal_hash", update.call_args.args[0])
