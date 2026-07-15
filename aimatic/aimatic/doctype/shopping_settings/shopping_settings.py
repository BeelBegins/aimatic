from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class ShoppingSettings(Document):
	def validate(self):
		if cint(self.allow_self_registration):
			if not self.registration_customer_group:
				frappe.throw(_("Registration Customer Group is required when self-registration is enabled"))
			if not self.registration_territory:
				frappe.throw(_("Registration Territory is required when self-registration is enabled"))
		if self.web_redirect_uri:
			parsed = urlparse(self.web_redirect_uri)
			if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
				frappe.throw(_("Web OAuth Redirect URI must be an absolute HTTPS URL without a fragment"))

	def on_update(self):
		self._sync_oauth_redirects()

	def _sync_oauth_redirects(self):
		client_name = frappe.db.get_value("OAuth Client", {"app_name": "Aimatic Shopping"})
		if not client_name:
			return
		client = frappe.get_doc("OAuth Client", client_name)
		native_uri = "tech.aimatic.shopping://oauth/callback"
		redirects = [native_uri]
		if self.web_redirect_uri:
			redirects.append(self.web_redirect_uri.strip())
		redirect_value = "\n".join(redirects)
		if client.redirect_uris != redirect_value or client.default_redirect_uri != native_uri:
			client.redirect_uris = redirect_value
			client.default_redirect_uri = native_uri
			client.save(ignore_permissions=True)
