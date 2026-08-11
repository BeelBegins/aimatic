# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aimatic.price_export.foodpanda_sftp import (
	_csv_filename,
	_remote_file_path,
	_sanitize_error,
	build_foodpanda_csv_bytes,
	build_foodpanda_csv_rows,
	run_scheduled_foodpanda_sftp_uploads,
	upload_foodpanda_csv,
)


class TestFoodpandaSftpCsv(unittest.TestCase):
	def test_builds_vendor_rows_and_skips_missing_barcode_or_price(self):
		rows = [
			{"item_code": "A", "barcode1": "111", "foodpanda_price": 10, "available_qty": 3},
			{"item_code": "B", "_barcodes": ["222"], "foodpanda_price": 0, "available_qty": 1},
			{"item_code": "C", "barcode1": "", "_barcodes": [], "foodpanda_price": 5, "available_qty": 0},
			{"item_code": "D", "_barcodes": ["333"], "foodpanda_price": 7.5, "available_qty": 0},
		]
		csv_rows, skipped = build_foodpanda_csv_rows("S1", rows=rows)
		self.assertEqual(skipped, 2)
		self.assertEqual(
			csv_rows,
			[
				{"barcode": "111", "sku": "", "price": 10.0, "active": 1, "quantity": 3.0},
				{"barcode": "333", "sku": "", "price": 7.5, "active": 0, "quantity": 0.0},
			],
		)

	def test_inactive_if_qty_lte_marks_low_stock_inactive(self):
		from aimatic.price_export.foodpanda_sftp import resolve_foodpanda_active

		self.assertEqual(resolve_foodpanda_active(3, 3), 0)
		self.assertEqual(resolve_foodpanda_active(4, 3), 1)
		self.assertEqual(resolve_foodpanda_active(0, 3), 0)
		self.assertEqual(resolve_foodpanda_active(3, None), 1)
		self.assertEqual(resolve_foodpanda_active(3, ""), 1)

		rows = [
			{"item_code": "A", "barcode1": "111", "foodpanda_price": 10, "available_qty": 3},
			{"item_code": "B", "barcode1": "222", "foodpanda_price": 10, "available_qty": 4},
			{"item_code": "C", "barcode1": "333", "foodpanda_price": 10, "available_qty": 0},
		]
		csv_rows, skipped = build_foodpanda_csv_rows("S1", rows=rows, inactive_if_qty_lte=3)
		self.assertEqual(skipped, 0)
		self.assertEqual(
			[(row["barcode"], row["active"], row["quantity"]) for row in csv_rows],
			[("111", 0, 3.0), ("222", 1, 4.0), ("333", 0, 0.0)],
		)

	def test_csv_bytes_include_header(self):
		payload = build_foodpanda_csv_bytes(
			[{"barcode": "111", "sku": "", "price": 10, "active": 1, "quantity": 2}]
		)
		text = payload.decode("utf-8")
		self.assertIn("barcode,sku,price,active,quantity", text)
		self.assertIn("111,,10,1,2", text)

	def test_filename_uses_scrubbed_branch(self):
		with patch("aimatic.price_export.foodpanda_sftp.frappe") as mock_frappe:
			mock_frappe.utils.getdate.return_value.isoformat.return_value = "2026-08-05"
			mock_frappe.scrub.return_value = "s1_ghouri_town_vip"
			self.assertEqual(
				_csv_filename("S1 - Ghouri Town VIP"),
				"foodpanda-s1_ghouri_town_vip-2026-08-05.csv",
			)

	def test_remote_path_joins(self):
		self.assertEqual(_remote_file_path("", "a.csv"), "a.csv")
		self.assertEqual(_remote_file_path("/", "a.csv"), "a.csv")
		self.assertEqual(_remote_file_path("/inbox", "a.csv"), "/inbox/a.csv")
		self.assertEqual(_remote_file_path("inbox", "a.csv"), "inbox/a.csv")

	def test_sanitize_redacts_password(self):
		self.assertEqual(
			_sanitize_error("auth failed for secret-password-value", password="secret-password-value"),
			"auth failed for [redacted]",
		)


class TestFoodpandaSftpUpload(unittest.TestCase):
	def _settings(self):
		return {
			"host": "sftp.example.com",
			"port": 22,
			"username": "FP_PK_test",
			"password": "secret",
			"remote_path": "/inbox",
			"enabled": 1,
		}

	@patch("aimatic.price_export.foodpanda_sftp._sftp_put")
	@patch("aimatic.price_export.foodpanda_sftp._write_upload_log")
	@patch("aimatic.price_export.foodpanda_sftp._update_branch_status")
	@patch("aimatic.price_export.foodpanda_sftp._load_sftp_settings")
	@patch("aimatic.price_export.foodpanda_sftp.build_foodpanda_csv_rows")
	def test_success_updates_status_and_log(
		self, mock_rows, mock_settings, mock_status, mock_log, mock_put
	):
		mock_settings.return_value = self._settings()
		mock_rows.return_value = (
			[{"barcode": "111", "sku": "", "price": 10, "active": 1, "quantity": 2}],
			1,
		)
		mock_put.return_value = "/inbox/foodpanda-s1-2026-08-05.csv"
		mock_log.return_value = SimpleNamespace(name="LOG-1")

		with patch("aimatic.price_export.foodpanda_sftp._csv_filename", return_value="foodpanda-s1-2026-08-05.csv"):
			result = upload_foodpanda_csv("S1", trigger="Branch")

		self.assertEqual(result["status"], "Success")
		self.assertEqual(result["log"], "LOG-1")
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["skipped_count"], 1)
		mock_status.assert_called_once_with("S1", success=True)
		mock_put.assert_called_once()

	@patch("aimatic.price_export.foodpanda_sftp._sftp_put")
	@patch("aimatic.price_export.foodpanda_sftp._write_upload_log")
	@patch("aimatic.price_export.foodpanda_sftp._update_branch_status")
	@patch("aimatic.price_export.foodpanda_sftp._load_sftp_settings")
	@patch("aimatic.price_export.foodpanda_sftp.build_foodpanda_csv_rows")
	def test_failure_sanitizes_and_logs(
		self, mock_rows, mock_settings, mock_status, mock_log, mock_put
	):
		mock_settings.return_value = self._settings()
		mock_rows.return_value = ([], 0)
		mock_put.side_effect = Exception("login failed with secret")
		mock_log.return_value = SimpleNamespace(name="LOG-FAIL")

		with patch("aimatic.price_export.foodpanda_sftp._csv_filename", return_value="foodpanda-s1.csv"):
			result = upload_foodpanda_csv("S1", trigger="Report")

		self.assertEqual(result["status"], "Failed")
		self.assertNotIn("secret", result["error"])
		mock_status.assert_called_once_with("S1", success=False, error=result["error"])
		self.assertEqual(mock_log.call_args.kwargs["status"], "Failed")

	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_missing_credentials_throw(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = SimpleNamespace(
			custom_fp_sftp_enabled=1,
			custom_fp_sftp_host="",
			custom_fp_sftp_port=22,
			custom_fp_sftp_username="",
			custom_fp_sftp_remote_path="",
		)
		branch_doc = MagicMock()
		branch_doc.get_password.return_value = None
		mock_frappe.get_doc.return_value = branch_doc
		mock_frappe._ = lambda x: x
		mock_frappe.utils.comma_and = lambda items: ", ".join(str(i) for i in items)

		def throw(msg):
			raise ValueError(msg)

		mock_frappe.throw.side_effect = throw

		from aimatic.price_export import foodpanda_sftp as mod

		with self.assertRaises(ValueError):
			mod._load_sftp_settings("S1")

	@patch("aimatic.price_export.foodpanda_sftp.upload_foodpanda_csv")
	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_scheduler_skips_disabled_and_isolates_failures(self, mock_frappe, mock_upload):
		mock_frappe.get_all.return_value = ["S1", "S2"]
		mock_upload.side_effect = [
			{"status": "Success", "log": "L1"},
			Exception("boom"),
		]
		mock_frappe.get_doc.return_value.get_password.return_value = None
		mock_frappe.get_traceback.return_value = "traceback"
		mock_frappe.utils.getdate.return_value.isoformat.return_value = "2026-08-05"

		with (
			patch(
				"aimatic.price_export.foodpanda_sftp._is_branch_due_for_scheduled_upload",
				return_value=True,
			),
			patch("aimatic.price_export.foodpanda_sftp._write_upload_log") as mock_log,
			patch("aimatic.price_export.foodpanda_sftp._update_branch_status") as mock_status,
			patch("aimatic.price_export.foodpanda_sftp._csv_filename", return_value="x.csv"),
			patch("aimatic.price_export.foodpanda_sftp._sanitize_error", return_value="boom"),
		):
			results = run_scheduled_foodpanda_sftp_uploads()

		self.assertEqual(results[0]["status"], "Success")
		self.assertEqual(results[1]["status"], "Failed")
		mock_frappe.get_all.assert_called_once()
		self.assertEqual(mock_frappe.get_all.call_args.kwargs["filters"], {"custom_fp_sftp_enabled": 1})
		mock_log.assert_called_once()
		mock_status.assert_called_once_with("S2", success=False, error="boom")

	@patch("aimatic.price_export.foodpanda_sftp.upload_foodpanda_csv")
	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_scheduler_skips_branches_not_due(self, mock_frappe, mock_upload):
		mock_frappe.get_all.return_value = ["S1", "S2"]

		def due(branch, now=None):
			return branch == "S2"

		with patch(
			"aimatic.price_export.foodpanda_sftp._is_branch_due_for_scheduled_upload",
			side_effect=due,
		):
			mock_upload.return_value = {"status": "Success", "log": "L2"}
			results = run_scheduled_foodpanda_sftp_uploads()

		self.assertEqual(results[0], {"branch": "S1", "status": "Skipped"})
		self.assertEqual(results[1]["status"], "Success")
		mock_upload.assert_called_once_with(
			"S2", rows=None, trigger="Scheduler", require_enabled=True
		)

	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_branch_due_requires_schedule_time_and_not_uploaded_today(self, mock_frappe):
		from datetime import datetime, time
		from aimatic.price_export.foodpanda_sftp import _is_branch_due_for_scheduled_upload

		now = datetime(2026, 8, 5, 10, 0, 0)
		mock_frappe.db.get_value.side_effect = [
			# first call inside _is_branch_due: enabled + schedule
			SimpleNamespace(custom_fp_sftp_enabled=1, custom_fp_sftp_schedule_time=time(9, 30)),
			# _already_uploaded_today last_upload
			None,
		]

		with (
			patch("aimatic.price_export.foodpanda_sftp.now_datetime", return_value=now),
			patch("aimatic.price_export.foodpanda_sftp.getdate", side_effect=lambda d: d.date() if hasattr(d, "date") else d),
			patch("aimatic.price_export.foodpanda_sftp.get_time", return_value=time(9, 30)),
			patch(
				"aimatic.price_export.foodpanda_sftp.get_datetime",
				return_value=datetime(2026, 8, 5, 9, 30, 0),
			),
		):
			self.assertTrue(_is_branch_due_for_scheduled_upload("S1", now=now))

		mock_frappe.db.get_value.side_effect = [
			SimpleNamespace(custom_fp_sftp_enabled=1, custom_fp_sftp_schedule_time=None),
		]
		self.assertFalse(_is_branch_due_for_scheduled_upload("S1", now=now))


if __name__ == "__main__":
	unittest.main()
