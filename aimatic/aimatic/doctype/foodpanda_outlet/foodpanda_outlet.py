import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class FoodpandaOutlet(Document):
	def validate(self):
		self.chain_id = (self.chain_id or "").strip()
		self.vendor_id = (self.vendor_id or "").strip()
		self.sftp_filename_prefix = (self.sftp_filename_prefix or "").strip()
		if not self.chain_id:
			self.chain_id = (
				frappe.db.get_single_value("Foodpanda Settings", "chain_id") or ""
			).strip()
		needs_chain = (
			cint(self.catalog_sync_enabled)
			or cint(self.order_ingestion_enabled)
			or cint(self.sftp_enabled)
		)
		if needs_chain and not self.chain_id:
			frappe.throw(
				_("Chain ID is required on Foodpanda Outlet {0} (each branch can have its own chain)").format(
					self.branch or self.name
				)
			)
		if self.sftp_filename_prefix and (
			"/" in self.sftp_filename_prefix or "\\" in self.sftp_filename_prefix
		):
			frappe.throw(_("SFTP Filename Prefix cannot contain a path separator"))
