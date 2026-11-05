"""Small DB-free checks for the till void log endpoint (log_pos_voids)."""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aimatic.offline_pos import api

PROFILE = SimpleNamespace(
	name="Counter 1", company="SZL", get=lambda k: {"branch": "Main", "currency": "PKR"}.get(k)
)


def event(**kw):
	ev = {
		"eventId": "VOID-1",
		"kind": "line_void",
		"createdAt": "2026-11-05T09:30:00.000Z",
		"cashierUser": "cashier@szl",
		"cashierName": "Ali",
		"authorizedBy": "cashier_privilege",
		"totalAmount": 1,  # deliberately wrong: the server must recompute
		"lines": [
			{"itemCode": "ITEM-1", "itemName": "Milk", "uom": "Nos", "qty": 3, "rate": 120.5, "amount": 1}
		],
	}
	ev.update(kw)
	return ev


class TestVoidLogDoc(unittest.TestCase):
	def build(self, ev, is_bearer=False, existing=("cashier@szl", "ITEM-1")):
		db = MagicMock()
		db.exists.side_effect = lambda doctype, name: name in existing
		with (
			patch.object(api.frappe, "db", db),
			patch.object(api.frappe, "get_cached_doc", return_value=PROFILE),
			patch.object(api.frappe, "get_doc", side_effect=lambda d: d),
			patch.object(
				api.frappe,
				"session",
				SimpleNamespace(user="android-cashier@szl" if is_bearer else "terminal@szl"),
			),
			patch.object(
				api.frappe, "get_system_settings", side_effect=lambda k: {"time_zone": "Asia/Karachi"}.get(k)
			),
		):
			return api._build_void_log_doc(ev, PROFILE, is_bearer)

	def test_utc_time_is_stored_as_karachi_time(self):
		self.assertEqual(self.build(event())["occurred_at"], datetime(2026, 11, 5, 14, 30))

	def test_amount_is_recomputed_not_trusted(self):
		doc = self.build(event())
		self.assertEqual(doc["items"][0]["amount"], 361.5)
		self.assertEqual(doc["total_amount"], 361.5)

	def test_electron_unknown_cashier_is_kept_only_as_reported(self):
		doc = self.build(event(cashierUser="ghost@szl"))
		self.assertIsNone(doc["cashier_user"])
		self.assertEqual(doc["reported_cashier_user"], "ghost@szl")
		self.assertEqual(doc["transport_user"], "terminal@szl")

	def test_bearer_cashier_comes_from_session(self):
		self.assertEqual(self.build(event(), is_bearer=True)["cashier_user"], "android-cashier@szl")

	def test_reason_is_stored_when_sent_and_blank_otherwise(self):
		self.assertEqual(
			self.build(event(reason="  Customer changed mind  "))["reason"], "Customer changed mind"
		)
		self.assertEqual(self.build(event())["reason"], "")

	def test_invalid_events_are_rejected(self):
		for bad in (event(eventId=""), event(kind="refund"), event(authorizedBy="me"), event(lines="x")):
			with self.assertRaises(ValueError):
				self.build(bad)


class TestLogPosVoids(unittest.TestCase):
	def call(self, events, stored=()):
		db = MagicMock()
		db.exists.side_effect = lambda doctype, name: doctype == "POS Void Log" and name in stored
		inserted = []
		built = MagicMock(
			side_effect=lambda ev, *_: MagicMock(insert=lambda **_: inserted.append(ev["eventId"]))
		)
		with (
			patch.object(api, "_require_login"),
			patch.object(api, "_is_bearer_authenticated_request", return_value=False),
			patch.object(api, "_get_pos_profile_or_throw", return_value=PROFILE),
			patch.object(api.frappe, "has_permission", return_value=True),
			patch.object(api.frappe, "db", db),
			patch.object(api.frappe, "log_error"),
			patch.object(api, "_build_void_log_doc", built),
		):
			return api.log_pos_voids("Counter 1", api.json.dumps(events)), inserted

	def test_already_stored_event_is_accepted_without_second_insert(self):
		result, inserted = self.call([{"eventId": "VOID-1"}, {"eventId": "VOID-2"}], stored={"VOID-1"})
		self.assertEqual(result["accepted"], ["VOID-1", "VOID-2"])
		self.assertEqual(inserted, ["VOID-2"])

	def test_failed_event_is_not_accepted(self):
		result, _ = self.call(["not an object"])
		self.assertEqual(result["accepted"], [])
		self.assertEqual(len(result["rejected"]), 1)


if __name__ == "__main__":
	unittest.main()
