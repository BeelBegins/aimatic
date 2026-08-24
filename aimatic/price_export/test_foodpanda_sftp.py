# Copyright (c) 2026, Ai Matic and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aimatic.price_export.foodpanda_sftp import (
	_remote_file_path,
	_sanitize_error,
	build_foodpanda_csv_bytes,
	build_foodpanda_csv_rows,
	csv_filename,
	run_scheduled_foodpanda_sftp_uploads,
	sanitize_sftp_filename_prefix,
	upload_foodpanda_csv,
)


class TestFoodpandaSftpCsv(unittest.TestCase):
	@patch("aimatic.price_export.foodpanda_sftp._sku_map_for_branch", return_value={})
	def test_builds_vendor_rows_and_skips_missing_barcode_or_price(self, _sku_map):
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
				{"sku": "", "barcode": "111", "price": 10.0, "active": 1, "quantity": 3},
				{"sku": "", "barcode": "333", "price": 7.5, "active": 0, "quantity": 0},
			],
		)

	@patch("aimatic.price_export.foodpanda_sftp._sku_map_for_branch", return_value={"A": "FP-A"})
	def test_fills_mapped_sku(self, _sku_map):
		rows = [{"item_code": "A", "barcode1": "111", "foodpanda_price": 10, "available_qty": 2.9}]
		csv_rows, skipped = build_foodpanda_csv_rows("S1", rows=rows)
		self.assertEqual(skipped, 0)
		self.assertEqual(csv_rows[0]["sku"], "FP-A")
		self.assertEqual(csv_rows[0]["quantity"], 2)

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
		with patch("aimatic.price_export.foodpanda_sftp._sku_map_for_branch", return_value={}):
			csv_rows, skipped = build_foodpanda_csv_rows("S1", rows=rows, inactive_if_qty_lte=3)
		self.assertEqual(skipped, 0)
		self.assertEqual(
			[(row["barcode"], row["active"], row["quantity"]) for row in csv_rows],
			[("111", 0, 3), ("222", 1, 4), ("333", 0, 0)],
		)

	def test_csv_bytes_include_header(self):
		payload = build_foodpanda_csv_bytes(
			[{"sku": "", "barcode": "111", "price": 10, "active": 1, "quantity": 2}]
		)
		text = payload.decode("utf-8")
		self.assertIn("sku,barcode,price,active,quantity", text)
		self.assertIn(",111,10,1,2", text)

	def test_filename_is_prefix_underscore_vendor(self):
		self.assertEqual(csv_filename("rg26"), "catalog_rg26.csv")
		self.assertEqual(csv_filename("rg26", prefix="siezal"), "siezal_rg26.csv")
		self.assertEqual(sanitize_sftp_filename_prefix("catalog.csv"), "catalog")
		self.assertEqual(sanitize_sftp_filename_prefix(""), "catalog")

	def test_remote_path_joins(self):
		self.assertEqual(_remote_file_path("", "a.csv"), "a.csv")
		self.assertEqual(_remote_file_path("/", "a.csv"), "a.csv")
		self.assertEqual(_remote_file_path("/inbox", "a.csv"), "/inbox/a.csv")
		self.assertEqual(_remote_file_path("Catalog", "catalog_rg26.csv"), "Catalog/catalog_rg26.csv")

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
			"remote_path": "Catalog",
			"filename_prefix": "catalog",
			"vendor_id": "rg26",
			"outlet": "Ghouri Town VIP",
			"enabled": 1,
		}

	@patch("aimatic.price_export.foodpanda_sftp._sftp_put")
	@patch("aimatic.price_export.foodpanda_sftp._write_upload_log")
	@patch("aimatic.price_export.foodpanda_sftp._update_outlet_status")
	@patch("aimatic.price_export.foodpanda_sftp._load_sftp_settings")
	@patch("aimatic.price_export.foodpanda_sftp.build_foodpanda_csv_rows")
	def test_success_updates_status_and_log(self, mock_rows, mock_settings, mock_status, mock_log, mock_put):
		mock_settings.return_value = self._settings()
		mock_rows.return_value = (
			[{"sku": "", "barcode": "111", "price": 10, "active": 1, "quantity": 2}],
			1,
		)
		mock_put.return_value = "Catalog/catalog_rg26.csv"
		mock_log.return_value = SimpleNamespace(name="LOG-1")

		result = upload_foodpanda_csv("S1", trigger="Branch")

		self.assertEqual(result["status"], "Success")
		self.assertEqual(result["filename"], "catalog_rg26.csv")
		self.assertEqual(result["log"], "LOG-1")
		self.assertEqual(result["row_count"], 1)
		self.assertEqual(result["skipped_count"], 1)
		mock_status.assert_called_once_with("S1", success=True)
		mock_put.assert_called_once()

	@patch("aimatic.price_export.foodpanda_sftp._sftp_put")
	@patch("aimatic.price_export.foodpanda_sftp._write_upload_log")
	@patch("aimatic.price_export.foodpanda_sftp._update_outlet_status")
	@patch("aimatic.price_export.foodpanda_sftp._load_sftp_settings")
	@patch("aimatic.price_export.foodpanda_sftp.build_foodpanda_csv_rows")
	def test_failure_sanitizes_and_logs(self, mock_rows, mock_settings, mock_status, mock_log, mock_put):
		mock_settings.return_value = self._settings()
		mock_rows.return_value = ([], 0)
		mock_put.side_effect = Exception("login failed with secret")
		mock_log.return_value = SimpleNamespace(name="LOG-FAIL")

		result = upload_foodpanda_csv("S1", trigger="Report")

		self.assertEqual(result["status"], "Failed")
		self.assertNotIn("secret", result["error"])
		mock_status.assert_called_once_with("S1", success=False, error=result["error"])
		self.assertEqual(mock_log.call_args.kwargs["status"], "Failed")

	@patch("aimatic.price_export.foodpanda_sftp._", new=lambda x: x)
	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_missing_credentials_throw(self, mock_frappe):
		mock_frappe.db.exists.return_value = True
		mock_frappe.db.get_value.return_value = "Outlet-1"
		outlet = SimpleNamespace(
			sftp_enabled=1,
			sftp_filename_prefix="",
			vendor_id="rg26",
			name="Outlet-1",
		)
		settings = MagicMock()
		settings.sftp_host = ""
		settings.sftp_username = ""
		settings.sftp_remote_path = ""
		settings.sftp_port = 22
		settings.sftp_filename_prefix = "catalog"
		settings.get_password.return_value = None
		mock_frappe.get_doc.return_value = outlet
		mock_frappe.get_single.return_value = settings
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
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="O1", branch="S1", vendor_id="v1", sftp_filename_prefix=""),
			SimpleNamespace(name="O2", branch="S2", vendor_id="v2", sftp_filename_prefix=""),
		]
		mock_upload.side_effect = [
			{"status": "Success", "log": "L1"},
			Exception("boom"),
		]
		mock_frappe.get_single.return_value.get_password.return_value = None
		mock_frappe.get_traceback.return_value = "traceback"

		with (
			patch(
				"aimatic.price_export.foodpanda_sftp._is_branch_due_for_scheduled_upload",
				return_value=True,
			),
			patch("aimatic.price_export.foodpanda_sftp.now_datetime"),
			patch("aimatic.price_export.foodpanda_sftp._write_upload_log") as mock_log,
			patch("aimatic.price_export.foodpanda_sftp._update_outlet_status") as mock_status,
			patch("aimatic.price_export.foodpanda_sftp._sanitize_error", return_value="boom"),
		):
			results = run_scheduled_foodpanda_sftp_uploads()

		self.assertEqual(results[0]["status"], "Success")
		self.assertEqual(results[1]["status"], "Failed")
		mock_frappe.get_all.assert_called_once()
		self.assertEqual(mock_frappe.get_all.call_args.kwargs["filters"], {"sftp_enabled": 1})
		mock_log.assert_called_once()
		self.assertEqual(mock_log.call_args.kwargs["filename"], "catalog_v2.csv")
		mock_status.assert_called_once_with("S2", success=False, error="boom")

	@patch("aimatic.price_export.foodpanda_sftp.upload_foodpanda_csv")
	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_scheduler_skips_branches_not_due(self, mock_frappe, mock_upload):
		mock_frappe.get_all.return_value = [
			SimpleNamespace(name="O1", branch="S1", vendor_id="v1", sftp_filename_prefix=""),
			SimpleNamespace(name="O2", branch="S2", vendor_id="v2", sftp_filename_prefix=""),
		]

		def due(branch, now=None):
			return branch == "S2"

		with patch(
			"aimatic.price_export.foodpanda_sftp._is_branch_due_for_scheduled_upload",
			side_effect=due,
		), patch("aimatic.price_export.foodpanda_sftp.now_datetime"):
			mock_upload.return_value = {"status": "Success", "log": "L2"}
			results = run_scheduled_foodpanda_sftp_uploads()

		self.assertEqual(results[0], {"branch": "S1", "status": "Skipped"})
		self.assertEqual(results[1]["status"], "Success")
		mock_upload.assert_called_once_with("S2", rows=None, trigger="Scheduler", require_enabled=True)

	@patch("aimatic.price_export.foodpanda_sftp.frappe")
	def test_branch_due_requires_schedule_time_and_not_uploaded_today(self, mock_frappe):
		from datetime import datetime, time

		from aimatic.price_export.foodpanda_sftp import _is_branch_due_for_scheduled_upload

		now = datetime(2026, 8, 5, 10, 0, 0)
		mock_frappe.db.get_value.side_effect = [
			SimpleNamespace(sftp_enabled=1, sftp_schedule_time=time(9, 30)),
			None,
		]

		with (
			patch("aimatic.price_export.foodpanda_sftp.now_datetime", return_value=now),
			patch(
				"aimatic.price_export.foodpanda_sftp.getdate",
				side_effect=lambda d: d.date() if hasattr(d, "date") else d,
			),
			patch("aimatic.price_export.foodpanda_sftp.get_time", return_value=time(9, 30)),
			patch(
				"aimatic.price_export.foodpanda_sftp.get_datetime",
				return_value=datetime(2026, 8, 5, 9, 30, 0),
			),
		):
			self.assertTrue(_is_branch_due_for_scheduled_upload("S1", now=now))

		mock_frappe.db.get_value.side_effect = [
			SimpleNamespace(sftp_enabled=1, sftp_schedule_time=None),
		]
		self.assertFalse(_is_branch_due_for_scheduled_upload("S1", now=now))


if __name__ == "__main__":
	unittest.main()
