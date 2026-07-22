import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MobileSalesDeliveryLocation(Document):
	def validate(self):
		if flt(self.minimum_order_value) < 0:
			frappe.throw(_("Minimum Order Value cannot be negative"))
		address = frappe.get_doc("Address", self.address)
		if address.disabled:
			frappe.throw(_("Address {0} is disabled").format(self.address))
		if not address.has_link("Customer", self.customer):
			frappe.throw(_("Address {0} is not linked to customer {1}").format(self.address, self.customer))
		if frappe.db.exists(
			"Mobile Sales Delivery Location",
			{"customer": self.customer, "address": self.address, "name": ["!=", self.name]},
		):
			frappe.throw(_("This customer delivery address is already configured"))
		if self.is_default and frappe.db.exists(
			"Mobile Sales Delivery Location",
			{"customer": self.customer, "is_default": 1, "enabled": 1, "name": ["!=", self.name]},
		):
			frappe.throw(_("Only one enabled default delivery location is allowed per customer"))

