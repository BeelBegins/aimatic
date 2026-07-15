import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aimatic.shopping import product_images


class TestShoppingProductImages(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.original_user = frappe.session.user
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user(self.original_user)
		super().tearDown()

	def test_job_status_is_visible_only_to_its_owner(self):
		job_id = frappe.generate_hash(length=20)
		product_images._set_status(job_id, {"owner": "Administrator", "status": "queued"})
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, product_images._get_status, job_id)

	@patch("aimatic.shopping.product_images.frappe.get_doc")
	def test_approval_rejects_an_attachment_for_another_product(self, get_doc):
		job_id = frappe.generate_hash(length=20)
		product_images._set_status(job_id, {
			"owner": "Administrator",
			"product": "ITEM-1",
			"result_file": "FILE-1",
			"status": "ready",
		})
		product = MagicMock(name="product")
		product.name = "ITEM-1"
		file_doc = MagicMock(name="file")
		file_doc.attached_to_doctype = "Shopping Product"
		file_doc.attached_to_name = "ITEM-2"
		get_doc.side_effect = [product, file_doc]

		self.assertRaises(frappe.PermissionError, product_images.approve_background_removal, job_id)
		product.save.assert_not_called()

	def test_status_payload_is_json_and_expires(self):
		with patch.object(frappe.cache, "set_value") as set_value:
			product_images._set_status("job", {"owner": "Administrator", "status": "processing"})
			payload = json.loads(set_value.call_args.args[1])
			self.assertEqual(payload["status"], "processing")
			self.assertEqual(set_value.call_args.kwargs["expires_in_sec"], 24 * 60 * 60)
