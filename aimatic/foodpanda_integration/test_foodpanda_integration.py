import json
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
		"api_host": "https://foodpanda.partner.deliveryhero.io",
		"chain_id": "chain-1",
		"client_id": "client-1",
		"catalog_locale": "en_PK",
		"allow_product_creation": False,
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
	def test_sync_item_skips_when_content_hash_unchanged(
		self, mock_frappe, mock_branch_defaults, mock_price_list
	):
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
		expected_payload = {
			"sku": "ITEM-1",
			"active": True,
			"price": 15.0,
			"quantity": 5,
			"max_sales_quantity": 1,
		}
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
		existing_product = Mock(
			sync_status="Pending", content_hash="stale-hash", foodpanda_product_id="ITEM-1"
		)
		existing_product.db_set.side_effect = lambda values: [
			setattr(existing_product, key, value) for key, value in values.items()
		]
		job_doc = Mock()
		mock_frappe.get_doc.side_effect = [outlet, existing_product, job_doc]
		settings = _settings()
		mock_client.get_settings.return_value = settings
		submit_response = Mock(status_code=202, json=Mock(return_value={"job_id": "job-123"}))
		mock_client.request.return_value = submit_response

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(mock_client.request.call_count, 1)
		submit_method, submit_path = mock_client.request.call_args.args
		self.assertEqual(submit_method, "PUT")
		self.assertIn("vendor-1", submit_path)
		self.assertEqual(existing_product.db_set.call_args.args[0]["sync_status"], "Pending")
		self.assertEqual(
			result,
			{
				"status": "Pending",
				"job_id": "job-123",
				"source": "Foodpanda API",
				"api_response": {"http_status": 202, "body": {"job_id": "job-123"}},
			},
		)

	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_sync_item_returns_and_persists_product_creation_disabled_error(self, mock_frappe, mock_client):
		from aimatic.foodpanda_integration import catalog

		outlet = Mock(catalog_sync_enabled=1)
		product = Mock(foodpanda_product_id=None)
		mock_frappe.db.get_value.return_value = "existing-fp-product-name"
		mock_frappe.get_doc.side_effect = [outlet, product]
		mock_client.get_settings.return_value = _settings(allow_product_creation=False)

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(result["source"], "ERPNext validation")
		self.assertIn("Product creation is disabled", result["error"])
		product.db_set.assert_called_once_with({"sync_status": "Failed", "last_error": result["error"]})
		mock_client.request.assert_not_called()

	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_sync_item_returns_sanitized_foodpanda_error_body(
		self, mock_frappe, mock_branch_defaults, mock_price_list, mock_client
	):
		from aimatic.foodpanda_integration import catalog
		from aimatic.foodpanda_integration.client import FoodpandaAPIError

		outlet = Mock(catalog_sync_enabled=1, branch="Branch 1", vendor_id="vendor-1")
		product = Mock(foodpanda_product_id="ITEM-1", sync_status="Pending", content_hash="old")
		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.get_value.side_effect = [
			"existing-fp-product-name",
			_item_row(),
			15.0,
			{"actual_qty": 5, "reserved_qty": 0},
			None,
		]
		mock_frappe.get_doc.side_effect = [outlet, product]
		mock_client.get_settings.return_value = _settings()
		mock_client.request.side_effect = FoodpandaAPIError(
			"Foodpanda catalog request failed with HTTP 400",
			status_code=400,
			response_body={"error": "validation_error", "details": ["bad price"]},
		)

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(result["source"], "Foodpanda API")
		self.assertEqual(result["api_response"]["http_status"], 400)
		self.assertEqual(result["api_response"]["body"]["error"], "validation_error")
		product.db_set.assert_called_once_with(
			{"sync_status": "Failed", "last_error": "Foodpanda catalog request failed with HTTP 400"}
		)

	def test_maximum_sales_quantity_uses_quarter_with_one_and_thirty_six_limits(self):
		from aimatic.foodpanda_integration.catalog import _maximum_sales_quantity

		self.assertEqual(_maximum_sales_quantity(0), 0)
		self.assertEqual(_maximum_sales_quantity(1), 1)
		self.assertEqual(_maximum_sales_quantity(19), 4)
		self.assertEqual(_maximum_sales_quantity(1000), 36)

	def test_barcode_variants_drop_foodpanda_leading_zero(self):
		from aimatic.foodpanda_integration.catalog import _barcode_variants

		variants = set(_barcode_variants("08851932354998"))
		self.assertIn("08851932354998", variants)
		self.assertIn("8851932354998", variants)
		self.assertIn("08851932354998"[1:], variants)

	@patch("aimatic.foodpanda_integration.catalog._get_job_status")
	@patch("aimatic.foodpanda_integration.catalog._set_job_status")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_start_bulk_export_passes_export_job_id_not_enqueue_job_id(
		self, mock_frappe, _mock_set_status, mock_get_status
	):
		from aimatic.foodpanda_integration import catalog

		mock_frappe.get_doc.return_value = Mock(catalog_sync_enabled=1)
		mock_frappe.session.user = "Administrator"
		mock_get_status.side_effect = lambda job_id: {
			"job_id": job_id,
			"outlet": "Outlet-1",
			"owner": "Administrator",
			"status": "queued",
			"synced": 0,
			"failed": 0,
			"total": 0,
		}

		status = catalog.start_bulk_export("Outlet-1")

		self.assertEqual(status["status"], "queued")
		self.assertTrue(status["job_id"])
		kwargs = mock_frappe.enqueue.call_args.kwargs
		self.assertEqual(kwargs["outlet_name"], "Outlet-1")
		self.assertEqual(kwargs["export_job_id"], status["job_id"])
		self.assertNotIn("job_id", kwargs)

	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_iter_remote_catalog_uses_page_not_page_number(self, mock_frappe, mock_client):
		from aimatic.foodpanda_integration import catalog

		mock_frappe.get_doc.return_value = Mock(vendor_id="vendor-1")
		mock_client.get_settings.return_value = _settings()
		mock_client.request.side_effect = [
			Mock(
				json=Mock(
					return_value={
						"page_number": 1,
						"page_size": 500,
						"total_pages": 2,
						"products": [{"sku": "A", "barcodes": ["1"]}],
					}
				)
			),
			Mock(
				json=Mock(
					return_value={
						"page_number": 2,
						"page_size": 500,
						"total_pages": 2,
						"products": [{"sku": "B", "barcodes": ["2"]}],
					}
				)
			),
		]

		products = list(catalog.iter_remote_catalog_products("Outlet-1", page_size=500, source="get"))

		self.assertEqual([p["sku"] for p in products], ["A", "B"])
		first_params = mock_client.request.call_args_list[0].kwargs["params"]
		second_params = mock_client.request.call_args_list[1].kwargs["params"]
		self.assertEqual(first_params, {"page": 1, "page_size": 500})
		self.assertEqual(second_params, {"page": 2, "page_size": 500})
		self.assertNotIn("page_number", first_params)

	def test_resolve_items_matches_local_barcode_without_leading_zero(self):
		from unittest.mock import patch

		from aimatic.foodpanda_integration.catalog import _resolve_items_for_remote_barcodes

		index = {"8851932354998": {"STO-ITEM-2026-09410"}}
		with patch("aimatic.foodpanda_integration.catalog.frappe") as mock_frappe:
			mock_frappe.db.exists.return_value = False
			items, matched = _resolve_items_for_remote_barcodes(["08851932354998"], index)
		self.assertEqual(items, ["STO-ITEM-2026-09410"])
		self.assertEqual(matched, "8851932354998")

	@patch("aimatic.foodpanda_integration.catalog.build_update_payload")
	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_create_payload_prefers_shopping_public_name(self, mock_frappe, mock_client, mock_update_payload):
		from aimatic.foodpanda_integration import catalog

		mock_frappe.db.get_value.side_effect = [
			_item_row(item_name="Internal item name"),
			"category-1",
			"PIM public name",
		]
		mock_frappe.get_all.return_value = ["6281001234567"]
		mock_client.get_settings.return_value = _settings(catalog_locale="en_PK")
		mock_update_payload.return_value = {
			"sku": "6281001234567",
			"active": True,
			"price": 15.0,
			"quantity": 5,
			"max_sales_quantity": 1,
		}

		payload = catalog.build_create_payload("ITEM-1", Mock())

		self.assertEqual(payload["title"], {"en_PK": "PIM public name"})
		self.assertEqual(payload["categories"], ["category-1"])
		self.assertEqual(payload["barcodes"], ["6281001234567"])
		mock_update_payload.assert_called_once()
		self.assertEqual(mock_update_payload.call_args.kwargs.get("foodpanda_sku"), "6281001234567")

	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_sync_item_skipped_when_catalog_sync_disabled(self, mock_frappe, mock_client):
		from aimatic.foodpanda_integration import catalog

		mock_frappe.get_doc.return_value = Mock(catalog_sync_enabled=0)

		result = catalog.sync_item("ITEM-1", "Outlet-1")

		self.assertEqual(result["status"], "Skipped")
		mock_client.request.assert_not_called()


class TestWebhookAuthorization(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.webhooks.frappe")
	def test_exact_authorization_value_passes(self, mock_frappe):
		from aimatic.foodpanda_integration import webhooks

		mock_frappe.get_single.return_value = Mock(get_password=Mock(return_value="whsec"))
		webhooks.verify_webhook_authorization("whsec")

		mock_frappe.throw.assert_not_called()

	@patch("aimatic.foodpanda_integration.webhooks.frappe")
	def test_bearer_authorization_matches_raw_secret(self, mock_frappe):
		from aimatic.foodpanda_integration import webhooks

		mock_frappe.get_single.return_value = Mock(get_password=Mock(return_value="whsec"))
		self.assertTrue(webhooks.authorization_matches_webhook_secret("Bearer whsec", "whsec"))
		webhooks.verify_webhook_authorization("Bearer whsec")
		mock_frappe.throw.assert_not_called()

	@patch("aimatic.foodpanda_integration.webhooks.frappe")
	def test_auth_hook_sets_user_for_catalog_webhook_bearer(self, mock_frappe):
		from aimatic.foodpanda_integration import webhooks

		mock_frappe.local.request = Mock(
			path="/api/method/aimatic.foodpanda_integration.api.foodpanda_catalog_webhook"
		)
		mock_frappe.session.user = "Guest"
		mock_frappe.get_request_header.return_value = "Bearer whsec"
		mock_frappe.get_single.return_value = Mock(get_password=Mock(return_value="whsec"))

		webhooks.validate_foodpanda_webhook_auth()

		mock_frappe.set_user.assert_called_once_with("Administrator")

	@patch("aimatic.foodpanda_integration.webhooks._", new=_NOOP_TRANSLATE)
	@patch("aimatic.foodpanda_integration.webhooks.frappe")
	def test_invalid_authorization_throws(self, mock_frappe):
		from aimatic.foodpanda_integration import webhooks

		mock_frappe.get_single.return_value = Mock(get_password=Mock(return_value="whsec"))
		mock_frappe.throw.side_effect = frappe.PermissionError

		with self.assertRaises(frappe.PermissionError):
			webhooks.verify_webhook_authorization("not-the-right-secret")

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
		mock_frappe.db.exists.return_value = True  # Customer exists
		mock_frappe.db.get_value.side_effect = [
			"Company 1",  # Branch -> company
			"ITEM-1",  # Foodpanda Product mapping for SKU
			{"actual_qty": 1, "reserved_qty": 0},  # Bin: only 1 available
		]
		mock_frappe.throw.side_effect = frappe.ValidationError
		outlet = Mock(branch="Branch 1", name="Outlet-1")

		payload = {"order_id": "FP-1", "items": [{"sku": "vendorcode-1", "quantity": 5}]}

		with self.assertRaises(frappe.ValidationError):
			orders.make_order_from_webhook(payload, outlet)

	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_update_payload_uses_barcode_not_item_code(
		self, mock_frappe, mock_branch_defaults, mock_price_list
	):
		from aimatic.foodpanda_integration import catalog

		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.get_value.side_effect = [
			_item_row(),
			15.0,
			{"actual_qty": 8, "reserved_qty": 0},
		]
		mock_frappe.get_all.return_value = ["6281001234567"]
		payload = catalog.build_update_payload("STO-ITEM-1", Mock(branch="Branch 1"))
		self.assertEqual(payload["sku"], "6281001234567")
		self.assertEqual(payload["barcode"], "6281001234567")
		self.assertNotEqual(payload["sku"], "STO-ITEM-1")

	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_update_payload_quantity_is_integer(self, mock_frappe, mock_branch_defaults, mock_price_list):
		from aimatic.foodpanda_integration import catalog

		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.get_value.side_effect = [
			_item_row(),
			15.0,
			{"actual_qty": 3.4, "reserved_qty": 0},
		]
		mock_frappe.get_all.return_value = ["6281001234567"]
		payload = catalog.build_update_payload("STO-ITEM-1", Mock(branch="Branch 1"), foodpanda_sku="fmv1")
		self.assertEqual(payload["quantity"], 3)
		self.assertIsInstance(payload["quantity"], int)
		self.assertEqual(payload["max_sales_quantity"], 1)

	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_update_payload_omits_non_digit_barcode(self, mock_frappe, mock_branch_defaults, mock_price_list):
		from aimatic.foodpanda_integration import catalog

		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.get_value.side_effect = [
			_item_row(),
			15.0,
			{"actual_qty": 8, "reserved_qty": 0},
		]
		mock_frappe.get_all.return_value = ["SM1500", "NC2"]
		payload = catalog.build_update_payload("STO-ITEM-1", Mock(branch="Branch 1"), foodpanda_sku="fmv900")
		self.assertEqual(payload["sku"], "fmv900")
		self.assertNotIn("barcode", payload)

	@patch("aimatic.foodpanda_integration.catalog.flt", new=_stub_flt)
	@patch("aimatic.foodpanda_integration.catalog.get_or_create_branch_foodpanda_price_list")
	@patch("aimatic.foodpanda_integration.catalog.get_branch_defaults")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_update_payload_prefers_mapped_foodpanda_sku(
		self, mock_frappe, mock_branch_defaults, mock_price_list
	):
		from aimatic.foodpanda_integration import catalog

		mock_branch_defaults.return_value = {"finished_goods_warehouse": "WH-1"}
		mock_price_list.return_value = "Branch 1 Foodpanda Price List"
		mock_frappe.db.get_value.side_effect = [
			_item_row(),
			15.0,
			{"actual_qty": 8, "reserved_qty": 0},
		]
		mock_frappe.get_all.return_value = ["6281001234567"]
		payload = catalog.build_update_payload(
			"STO-ITEM-1", Mock(branch="Branch 1"), foodpanda_sku="vendorcode-99"
		)
		self.assertEqual(payload["sku"], "vendorcode-99")
		self.assertEqual(payload["barcode"], "6281001234567")


class TestOrderWebhookIdempotency(unittest.TestCase):
	@patch("aimatic.foodpanda_integration.api.webhooks")
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_duplicate_delivery_returns_existing_result_without_reprocessing(
		self, mock_frappe, mock_orders, mock_webhooks
	):
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = b'{"order_id":"FP-1","vendor_id":"v1","items":[]}'
		mock_frappe.db.get_value.return_value = frappe._dict(
			{"name": "LOG-1", "status": "Accepted", "sales_order": "SO-0001"}
		)

		result = api.foodpanda_order_webhook()

		self.assertEqual(result, {"status": "Accepted", "sales_order": "SO-0001", "duplicate": True})
		mock_orders.resolve_outlet.assert_not_called()
		mock_orders.make_order_from_webhook.assert_not_called()

	@patch("aimatic.foodpanda_integration.api.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.api.webhooks")
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_failed_order_creation_rejects_with_foodpanda_and_records_failure(
		self, mock_frappe, mock_orders, mock_webhooks
	):
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = (
			b'{"order_id":"FP-2","vendor_id":"v1","items":[{"sku":"X"}]}'
		)
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
	@patch("aimatic.foodpanda_integration.api.webhooks")
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_successful_order_creation_is_acknowledged_by_webhook_2xx(
		self, mock_frappe, mock_orders, mock_webhooks
	):
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = (
			b'{"order_id":"FP-3","vendor_id":"v1","items":[{"sku":"X"}]}'
		)
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

		self.assertEqual(result["status"], "Accepted")
		self.assertEqual(result["sales_order"], "SO-0099")
		self.assertIn("order_log", result)
		mock_orders.accept_order.assert_not_called()
		mock_orders.reject_order.assert_not_called()

	@patch("aimatic.foodpanda_integration.api.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.api.webhooks")
	@patch("aimatic.foodpanda_integration.api.orders")
	@patch("aimatic.foodpanda_integration.api.frappe")
	def test_ready_for_pickup_first_status_creates_sales_order(self, mock_frappe, mock_orders, mock_webhooks):
		"""Indirect / Pelican vendors send READY_FOR_PICKUP as the first webhook."""
		from aimatic.foodpanda_integration import api

		mock_frappe.request.get_data.return_value = (
			b'{"order_id":"FP-RFP","status":"READY_FOR_PICKUP","vendor_id":"v1",'
			b'"items":[{"sku":"X","pricing":{"quantity":1}}]}'
		)
		mock_frappe.db.get_value.return_value = None
		outlet = Mock()
		outlet.name = "Outlet-1"
		mock_orders.resolve_outlet.return_value = outlet
		log_doc = Mock()
		log_doc.name = "LOG-RFP"
		mock_frappe.get_doc.return_value = log_doc
		sales_order = Mock()
		sales_order.name = "SO-RFP"
		mock_orders.make_order_from_webhook.return_value = sales_order

		result = api.foodpanda_order_webhook()

		self.assertEqual(result["status"], "Accepted")
		self.assertEqual(result["sales_order"], "SO-RFP")
		mock_orders.make_order_from_webhook.assert_called_once()
		mock_orders.reject_order.assert_not_called()

	@patch("aimatic.foodpanda_integration.client.requests.post")
	@patch("aimatic.foodpanda_integration.client.frappe")
	def test_token_network_failure_is_wrapped_without_traceback_details(self, mock_frappe, mock_post):
		import requests

		from aimatic.foodpanda_integration.client import FoodpandaAPIError, _fetch_access_token

		mock_post.side_effect = requests.ConnectionError("dns details")
		with self.assertRaises(FoodpandaAPIError) as ctx:
			_fetch_access_token(_settings())
		self.assertIn("could not connect", str(ctx.exception))
		self.assertNotIn("dns details", str(ctx.exception))

	@patch("aimatic.foodpanda_integration.catalog.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.catalog.client")
	@patch("aimatic.foodpanda_integration.catalog.frappe")
	def test_add_products_uses_chain_path_and_vendors_array(self, mock_frappe, mock_client):
		from aimatic.foodpanda_integration import catalog

		mock_client.request.return_value = Mock(json=Mock(return_value={"job_id": "job-add"}))
		mock_frappe.get_doc.return_value = Mock()
		job_id = catalog._submit_catalog_job(
			_settings(), "POST", "chain-1", "vendor-1", {"sku": "ITEM-1"}, "Outlet-1"
		)
		self.assertEqual(job_id, "job-add")
		method, path = mock_client.request.call_args.args
		self.assertEqual((method, path), ("POST", "/v2/chains/chain-1/catalog"))
		self.assertEqual(
			mock_client.request.call_args.kwargs["json"],
			{"vendors": ["vendor-1"], "products": [{"sku": "ITEM-1"}]},
		)

	@patch("aimatic.foodpanda_integration.catalog_jobs.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.catalog_jobs.frappe")
	def test_catalog_callback_promotes_pending_hash(self, mock_frappe):
		from aimatic.foodpanda_integration import catalog_jobs

		mock_frappe.db.get_value.side_effect = [
			"job-add",  # Foodpanda Catalog Job name
			'["6281001234567"]',  # requested_skus submitted to Foodpanda
		]
		mock_frappe.get_all.return_value = [
			frappe._dict(
				{
					"name": "product-row",
					"item_code": "ITEM-1",
					"foodpanda_product_id": None,
					"pending_content_hash": "hash-1",
				}
			)
		]
		result = catalog_jobs.process_callback({"job_id": "job-add", "job_status": "COMPLETED"})
		self.assertEqual(result, {"job_id": "job-add", "status": "Completed", "products_updated": 1})
		mock_frappe.db.set_value.assert_any_call(
			"Foodpanda Product",
			"product-row",
			{
				"sync_status": "Synced",
				"content_hash": "hash-1",
				"pending_content_hash": "",
				"last_synced": _FIXED_DATETIME(),
				"last_error": "",
				"foodpanda_product_id": "6281001234567",
			},
		)

	@patch("aimatic.foodpanda_integration.orders.frappe")
	def test_nested_client_vendor_id_resolves_outlet(self, mock_frappe):
		from aimatic.foodpanda_integration import orders

		mock_frappe.db.get_value.return_value = "Outlet-1"
		outlet = Mock()
		mock_frappe.get_doc.return_value = outlet
		result = orders.resolve_outlet({"client": {"external_partner_config_id": "vendor-1"}})
		self.assertIs(result, outlet)
		self.assertEqual(mock_frappe.db.get_value.call_args.args[1]["vendor_id"], "vendor-1")

	@patch("aimatic.foodpanda_integration.orders.client")
	@patch("aimatic.foodpanda_integration.orders.frappe")
	def test_ready_for_pickup_uses_documented_status(self, mock_frappe, mock_client):
		from aimatic.foodpanda_integration import orders

		mock_frappe.db.get_value.return_value = frappe._dict(
			{
				"name": "LOG-1",
				"foodpanda_order_id": "FP-1",
			}
		)
		mock_client.get_settings.return_value = _settings()
		result = orders.push_status_update("SO-1", "Ready for Pickup")
		self.assertEqual(result, {"foodpanda_order_id": "FP-1", "status": "READY_FOR_PICKUP"})
		self.assertEqual(mock_client.request.call_args.kwargs["json"], {"status": "READY_FOR_PICKUP"})

	@patch("aimatic.foodpanda_integration.outlet.now_datetime", new=_FIXED_DATETIME)
	@patch("aimatic.foodpanda_integration.outlet.add_to_date")
	@patch("aimatic.foodpanda_integration.outlet.client")
	@patch("aimatic.foodpanda_integration.outlet.frappe")
	def test_busy_maps_to_closed_until(self, mock_frappe, mock_client, mock_add_to_date):
		from aimatic.foodpanda_integration import outlet

		remote_until = Mock()
		remote_until.isoformat.return_value = "2026-07-31T10:30:00"
		mock_add_to_date.return_value = remote_until
		mock_frappe.get_doc.return_value = Mock(vendor_id="vendor-1")
		mock_client.get_settings.return_value = _settings()
		outlet.push_outlet_status("Outlet-1", "Busy")
		payload = mock_client.request.call_args.kwargs["json"]
		self.assertEqual(payload["status"], "CLOSED_UNTIL")
		self.assertEqual(payload["closed_reason"], "TOO_BUSY_KITCHEN")


class TestCatalogExport(unittest.TestCase):
	def test_parse_export_payload_csv(self):
		from aimatic.foodpanda_integration.catalog_export import parse_export_payload

		raw = (
			b'"sku","barcode","price","active","maximum_sales_quantity"\n'
			b'"fmv5011","08851932355131","355.0","1","3"\n'
			b'"vendorcode-1","06295120046197","1049.0","0","6"\n'
		)
		products = parse_export_payload(raw)
		self.assertEqual(len(products), 2)
		self.assertEqual(products[0]["sku"], "fmv5011")
		self.assertEqual(products[0]["barcodes"], ["08851932355131"])
		self.assertEqual(products[0]["price"], 355.0)
		self.assertTrue(products[0]["active"])
		self.assertFalse(products[1]["active"])

	def test_parse_export_payload_products_object(self):
		from aimatic.foodpanda_integration.catalog_export import parse_export_payload

		raw = json.dumps(
			{
				"products": [
					{"sku": "fmv5011", "barcodes": ["08851932355131"], "title": {"en_PK": "Sunsilk"}},
					{"sku": "fmv5012", "barcode": "8851932355132"},
				]
			}
		).encode()
		products = parse_export_payload(raw)
		self.assertEqual(len(products), 2)
		self.assertEqual(products[0]["sku"], "fmv5011")
		self.assertEqual(products[0]["barcodes"], ["08851932355131"])
		self.assertEqual(products[0]["title"], "Sunsilk")

	def test_parse_export_payload_json_lines(self):
		from aimatic.foodpanda_integration.catalog_export import parse_export_payload

		raw = b'{"sku":"A","barcodes":["1"]}\n{"sku":"B","barcodes":["2"]}\n'
		products = parse_export_payload(raw)
		self.assertEqual([p["sku"] for p in products], ["A", "B"])

	@patch("aimatic.foodpanda_integration.catalog_export.iter_cached_export_products")
	def test_iter_remote_catalog_prefers_export_snapshot(self, mock_iter_export):
		from aimatic.foodpanda_integration import catalog

		mock_iter_export.return_value = [{"sku": "EXP-1", "barcodes": ["1"]}]
		products = list(catalog.iter_remote_catalog_products("Outlet-1", source="auto"))
		self.assertEqual(products[0]["sku"], "EXP-1")
		mock_iter_export.assert_called_once_with("Outlet-1")

	@patch("aimatic.foodpanda_integration.catalog_jobs.frappe")
	def test_export_webhook_enqueues_download(self, mock_frappe):
		from aimatic.foodpanda_integration import catalog_jobs

		mock_frappe.db.get_value.side_effect = ["JOB-1", "Export"]
		catalog_jobs.process_callback(
			{
				"job_id": "job-export-1",
				"job_status": "COMPLETED",
				"download_url": "https://example.com/export.json",
			}
		)
		mock_frappe.enqueue.assert_called_once()
		self.assertIn("download_and_store_export", mock_frappe.enqueue.call_args.args[0])
		self.assertEqual(
			mock_frappe.enqueue.call_args.kwargs.get("foodpanda_job_id"),
			"job-export-1",
		)
		self.assertNotIn("job_id", mock_frappe.enqueue.call_args.kwargs)


if __name__ == "__main__":
	unittest.main()
