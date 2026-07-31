import unittest
from unittest.mock import Mock, patch

import frappe

_NOOP_TRANSLATE = lambda text: text  # noqa: E731 - stand-in for frappe._, which needs a live site
_FIXED_DATETIME = lambda: "2026-07-31 10:00:00"  # noqa: E731
_FIXED_DATE = lambda: "2026-07-31"  # noqa: E731


def _stub_flt(value, precision=None, rounding_method=None):
	"""Stand-in for frappe.utils.flt: with a precision argument, the real flt
	routes through rounded() -> frappe.get_system_settings(), which needs a
	live site and silently returns 0.0 without one."""
	number = float(value or 0)
	return round(number, precision) if precision is not None else number


def _settings(**overrides):
	values = {
		"api_host": "https://api-sandbox.example",
		"chain_id": "chain-1",
		"client_id": "client-1",
		"request_timeout": 30,
		"maximum_retries": 2,
		"verify_ssl": True,
	}
	values.update({k: v for k, v in overrides.items() if k != "client_secret"})
	settings = Mock(**values)
	settings.get_password.return_value = overrides.get("client_secret", "s3cret")
	return settings


class TestClientTokenCache(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_cache_hit_skips_token_request(self, mock_frappe, mock_requests):
		from aimatic.foodpanda_integration.client import get_access_token

		mock_frappe.cache.get_value.return_value = b"cached-token"
		settings = _settings()

		token = get_access_token(settings)

		self.assertEqual(token, "cached-token")
		mock_requests.post.assert_not_called()

	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_cache_miss_fetches_and_caches_with_safety_margin(self, mock_frappe, mock_requests):
		from aimatic.foodpanda_integration.client import get_access_token

		mock_frappe.cache.get_value.return_value = None
		mock_requests.post.return_value = Mock(
			status_code=200, json=Mock(return_value={"access_token": "tok-123", "expires_in": 7200})
		)
		settings = _settings(client_id="cid-42")

		token = get_access_token(settings)

		self.assertEqual(token, "tok-123")
		mock_frappe.cache.set_value.assert_called_once_with(
			"aimatic:foodpanda:token:cid-42", "tok-123", expires_in_sec=7200 - 60
		)

	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_client_secret_never_appears_in_raised_error(self, mock_frappe, mock_requests):
		from aimatic.foodpanda_integration.client import FoodpandaAPIError, get_access_token

		mock_frappe.cache.get_value.return_value = None
		mock_requests.post.return_value = Mock(
			status_code=401, json=Mock(side_effect=ValueError), text="unauthorized"
		)
		settings = _settings(client_secret="super-secret-value")

		with self.assertRaises(FoodpandaAPIError) as ctx:
			get_access_token(settings)

		self.assertNotIn("super-secret-value", str(ctx.exception))
		self.assertNotIn("super-secret-value", str(ctx.exception.response_body))


class TestClientRequest(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.client.time")
	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_401_invalidates_cached_token_and_retries_once(self, mock_frappe, mock_requests, mock_time):
		from aimatic.foodpanda_integration.client import request

		mock_frappe.cache.get_value.return_value = b"stale-token"
		mock_requests.post.return_value = Mock(
			status_code=200, json=Mock(return_value={"access_token": "fresh-token", "expires_in": 7200})
		)
		unauthorized = Mock(status_code=401, ok=False)
		success = Mock(status_code=200, ok=True)
		mock_requests.request.side_effect = [unauthorized, success]
		settings = _settings()

		response = request("GET", "/v2/chains/chain-1/vendors/v1/status", settings=settings)

		self.assertIs(response, success)
		self.assertEqual(mock_requests.request.call_count, 2)
		first_headers = mock_requests.request.call_args_list[0].kwargs["headers"]
		second_headers = mock_requests.request.call_args_list[1].kwargs["headers"]
		self.assertEqual(first_headers["Authorization"], "Bearer stale-token")
		self.assertEqual(second_headers["Authorization"], "Bearer fresh-token")

	@patch("aimatic.foodpanda_integration.client.time")
	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_5xx_retries_then_succeeds(self, mock_frappe, mock_requests, mock_time):
		from aimatic.foodpanda_integration.client import request

		mock_frappe.cache.get_value.return_value = b"token"
		failure = Mock(status_code=503, ok=False)
		success = Mock(status_code=200, ok=True)
		mock_requests.request.side_effect = [failure, failure, success]
		settings = _settings(maximum_retries=3)

		response = request("GET", "/v2/chains/chain-1/vendors/v1/status", settings=settings)

		self.assertIs(response, success)
		self.assertEqual(mock_requests.request.call_count, 3)
		self.assertEqual(mock_time.sleep.call_count, 2)

	@patch("aimatic.foodpanda_integration.client.time")
	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_persistent_5xx_raises_after_max_retries(self, mock_frappe, mock_requests, mock_time):
		from aimatic.foodpanda_integration.client import FoodpandaAPIError, request

		mock_frappe.cache.get_value.return_value = b"token"
		failure = Mock(status_code=500, ok=False, json=Mock(side_effect=ValueError), text="boom")
		mock_requests.request.return_value = failure
		settings = _settings(maximum_retries=2)

		with self.assertRaises(FoodpandaAPIError):
			request("GET", "/v2/chains/chain-1/vendors/v1/status", settings=settings)

		self.assertEqual(mock_requests.request.call_count, 3)  # initial attempt + 2 retries

	@patch("aimatic.foodpanda_integration.client.time")
	@patch("aimatic.foodpanda_integration.client.requests")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_custom_headers_survive_a_retry(self, mock_frappe, mock_requests, mock_time):
		from aimatic.foodpanda_integration.client import request

		mock_frappe.cache.get_value.return_value = b"token"
		failure = Mock(status_code=503, ok=False)
		success = Mock(status_code=200, ok=True)
		mock_requests.request.side_effect = [failure, success]
		settings = _settings(maximum_retries=3)

		request(
			"GET",
			"/v2/chains/chain-1/vendors/v1/status",
			settings=settings,
			headers={"X-Custom": "abc"},
		)

		for call_args in mock_requests.request.call_args_list:
			self.assertEqual(call_args.kwargs["headers"]["X-Custom"], "abc")


def _item_row(**overrides):
	values = {
		"item_name": "Item One",
		"description": "",
		"item_group": "Products",
		"disabled": 0,
		"is_sales_item": 1,
	}
	values.update(overrides)
	return frappe._dict(values)


class TestCatalogSync(unittest.TestCase):
	def test_hash_payload_is_order_independent_and_stable(self):
		from aimatic.foodpanda_integration.catalog import hash_payload

		a = {"sku": "ITEM-1", "price": 10, "active": True}
		b = {"active": True, "price": 10, "sku": "ITEM-1"}

		self.assertEqual(hash_payload(a), hash_payload(b))
		self.assertNotEqual(hash_payload(a), hash_payload({**a, "price": 11}))

	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_sync_item_skips_when_content_hash_unchanged(self, mock_frappe, mock_branch_defaults, mock_price_list):
		from aimatic.foodpanda_integration import catalog

		outlet = Mock(catalog_sync_enabled=1, branch="Branch 1", vendor_id="vendor-1")
		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		# build_update_payload's call order: Item, Item Price, Bin, Item Barcode
		# (get_or_create_foodpanda_product's name lookup happens first).
		mock_frappe.db.get_value.side_effect = [
			"existing-fp-product-name",  # Foodpanda Product name lookup
			_item_row(),  # Item
			15.0,  # Item Price rate
			{"actual_qty": 5, "reserved_qty": 0},  # Bin
			None,  # Item Barcode - none on file
		]
		expected_payload = {"sku": "ITEM-1", "active": True, "price": 15.0, "quantity": 5.0}
		existing_product = Mock(
			sync_status="Synced",
			content_hash=catalog.hash_payload(expected_payload),
			foodpanda_product_id="ITEM-1",
		)
		mock_frappe.get_doc.side_effect = [outlet, existing_product]

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(result, {"status": "Synced", "skipped": True})

	@patch("aimatic.foodpanda_integration.catalog.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_sync_item_uses_update_path_when_product_id_already_set(
		self, mock_frappe, mock_branch_defaults, mock_price_list, mock_client
	):
		from aimatic.foodpanda_integration import catalog

		outlet = Mock(catalog_sync_enabled=1, branch="Branch 1", vendor_id="vendor-1")
		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.get_value.side_effect = [
			"existing-fp-product-name",
			_item_row(),
			15.0,
			{"actual_qty": 5, "reserved_qty": 0},
			None,
		]
		existing_product = Mock(sync_status="Pending", content_hash="stale-hash", foodpanda_product_id="ITEM-1")
		existing_product.db_set.side_effect = lambda values: [
			setattr(existing_product, key, value) for key, value in values.items()
		]
		mock_frappe.get_doc.side_effect = [outlet, existing_product]
		settings = _settings()
		mock_client.get_settings.return_value = settings
		submit_response = Mock(json=Mock(return_value={"job_id": "job-123"}))
		status_response = Mock(json=Mock(return_value={"status": "COMPLETED"}))
		mock_client.request.side_effect = [submit_response, status_response]

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(mock_client.request.call_count, 2)
		submit_method, submit_path = mock_client.request.call_args_list[0].args
		self.assertEqual(submit_method, "PUT")
		self.assertIn("vendor-1", submit_path)
		status_method, status_path = mock_client.request.call_args_list[1].args
		self.assertEqual(status_method, "GET")
		self.assertIn("job-123", status_path)
		self.assertEqual(existing_product.db_set.call_args.args[0]["sync_status"], "Synced")
		self.assertEqual(result, {"status": "Synced"})

	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_sync_item_skipped_when_catalog_sync_disabled(self, mock_frappe, mock_client):
		from aimatic.foodpanda_integration import catalog

		mock_frappe.get_doc.return_value = Mock(catalog_sync_enabled=0)

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(result["status"], "Skipped")
		mock_client.request.assert_not_called()


class TestOrdersWebhookSignature(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.orders.frappe")
	def test_valid_signature_passes(self, mock_frappe):
		import hashlib
		import hmac

		from aimatic.foodpanda_integration import orders

		mock_frappe.get_single.return_value = Mock(get_password=Mock(return_value="whsec"))
		body = b'{"order_id":"FP-1"}'
		signature = hmac.new(b"whsec", body, hashlib.sha256).hexdigest()

		orders.verify_webhook_signature(body, signature)  # must not raise

		mock_frappe.throw.assert_not_called()

	@patch("aimatic.foodpanda_integration.orders._", new=_NOOP_TRANSLATE)
	@patch("aimatic.foodpanda_integration.orders.frappe")
	def test_invalid_signature_throws(self, mock_frappe):
		from aimatic.foodpanda_integration import orders

		mock_frappe.get_single.return_value = Mock(get_password=Mock(return_value="whsec"))
		mock_frappe.throw.side_effect = frappe.PermissionError

		with self.assertRaises(frappe.PermissionError):
			orders.verify_webhook_signature(b'{"order_id":"FP-1"}', "not-the-right-signature")

	@patch("aimatic.foodpanda_integration.orders._", new=_NOOP_TRANSLATE)
	@patch("aimatic.foodpanda_integration.orders.frappe")
	def test_missing_vendor_id_throws(self, mock_frappe):
		from aimatic.foodpanda_integration import orders

		mock_frappe.throw.side_effect = frappe.ValidationError

		with self.assertRaises(frappe.ValidationError):
			orders.resolve_outlet({"order_id": "FP-1"})


class TestOrdersStockCheck(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.orders._", new=_NOOP_TRANSLATE)
	@patch("aimatic.foodpanda_integration.orders.nowdate", new=_FIXED_DATE)
	@patch("aimatic.foodpanda_integration.orders.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.orders.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.orders.frappe")
	def test_insufficient_stock_line_raises_instead_of_overselling(
		self, mock_frappe, mock_branch_defaults, mock_price_list
	):
		from aimatic.foodpanda_integration import orders

		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.exists.side_effect = [True, True]  # Customer exists, Item exists
		mock_frappe.db.get_value.side_effect = [
			"Company 1",  # Branch -> company
			{"actual_qty": 1, "reserved_qty": 0},  # Bin: only 1 available
		]
		mock_frappe.throw.side_effect = frappe.ValidationError
		outlet = Mock(branch="Branch 1")

		payload = {"order_id": "FP-1", "items": [{"sku": "ITEM-1", "quantity": 5}]}

		with self.assertRaises(frappe.ValidationError):
			orders.make_order_from_webhook(payload, outlet)


class TestOrderWebhookIdempotency(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_duplicate_delivery_returns_existing_result_without_reprocessing(self, mock_frappe, mock_orders):
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = b'{"order_id":"FP-1","vendor_id":"v1","items":[]}'
		mock_frappe.get_request_header.return_value = "sig"
		mock_frappe.db.get_value.return_value = frappe._dict({"status": "Accepted", "sales_order": "SO-0001"})

		result = api.foodpanda_order_webhook()

		self.assertEqual(result, {"status": "Accepted", "sales_order": "SO-0001", "duplicate": True})
		mock_orders.resolve_outlet.assert_not_called()
		mock_orders.make_order_from_webhook.assert_not_called()

	@patch("aimatic.foodpanda_integration.api.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_failed_order_creation_rejects_with_foodpanda_and_records_failure(self, mock_frappe, mock_orders):
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = b'{"order_id":"FP-2","vendor_id":"v1","items":[{"sku":"X"}]}'
		mock_frappe.get_request_header.return_value = "sig"
		mock_frappe.db.get_value.return_value = None  # no existing log row
		outlet = Mock()
		outlet.name = "Outlet-1"
		mock_orders.resolve_outlet.return_value = outlet
		log_doc = Mock()
		mock_frappe.get_doc.return_value = log_doc
		mock_orders.make_order_from_webhook.side_effect = frappe.ValidationError("Insufficient stock for X")

		result = api.foodpanda_order_webhook()

		self.assertEqual(result["status"], "Failed")
		mock_frappe.db.rollback.assert_called_once()
		log_doc.db_set.assert_any_call({"status": "Failed", "error": "Insufficient stock for X"})
		mock_orders.reject_order.assert_called_once_with(outlet, "FP-2", reason="Insufficient stock for X")

	@patch("aimatic.foodpanda_integration.api.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_successful_order_creation_accepts_with_foodpanda(self, mock_frappe, mock_orders):
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = b'{"order_id":"FP-3","vendor_id":"v1","items":[{"sku":"X"}]}'
		mock_frappe.get_request_header.return_value = "sig"
		mock_frappe.db.get_value.return_value = None
		outlet = Mock()
		outlet.name = "Outlet-1"
		mock_orders.resolve_outlet.return_value = outlet
		log_doc = Mock()
		mock_frappe.get_doc.return_value = log_doc
		sales_order = Mock()
		sales_order.name = "SO-0099"
		mock_orders.make_order_from_webhook.return_value = sales_order

		result = api.foodpanda_order_webhook()

		self.assertEqual(result, {"status": "Accepted", "sales_order": "SO-0099"})
		mock_orders.accept_order.assert_called_once_with(outlet, "FP-3")
		mock_orders.reject_order.assert_not_called()


if __name__ == "__main__":
	unittest.main()
