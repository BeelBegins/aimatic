import frappe
from frappe import _
from frappe.model.document import Document

from aimatic.foodpanda_integration.client import OFFICIAL_API_HOST


class FoodpandaSettings(Document):
	def validate(self):
		self.api_host = (self.api_host or OFFICIAL_API_HOST).rstrip("/")
		if self.api_host != OFFICIAL_API_HOST:
			frappe.throw(
				_(
					"Foodpanda API Host must be {0}; catalog testing uses a designated live test vendor, not a sandbox hostname."
				).format(OFFICIAL_API_HOST)
			)
		if self.enabled and not self.client_id:
			frappe.throw(_("Client ID is required when Foodpanda is enabled"))
		if self.request_timeout is not None and int(self.request_timeout) < 1:
			frappe.throw(_("Request Timeout must be at least one second"))
		if self.maximum_retries is not None and int(self.maximum_retries) < 0:
			frappe.throw(_("Maximum Retries cannot be negative"))
		prefix = (self.sftp_filename_prefix or "").strip()
		if "/" in prefix or "\\" in prefix:
			frappe.throw(_("SFTP Filename Prefix cannot contain a path separator"))
		self.sftp_filename_prefix = prefix
