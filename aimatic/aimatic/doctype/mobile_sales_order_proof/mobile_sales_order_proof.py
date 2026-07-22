import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MobileSalesOrderProof(Document):
	def validate(self):
		latitude = flt(self.latitude)
		longitude = flt(self.longitude)
		if latitude < -90 or latitude > 90:
			frappe.throw(_("Latitude must be between -90 and 90"))
		if longitude < -180 or longitude > 180:
			frappe.throw(_("Longitude must be between -180 and 180"))
		if frappe.db.exists(
			"Mobile Sales Order Proof",
			{"sales_order": self.sales_order, "name": ["!=", self.name]},
		):
			frappe.throw(_("Proof has already been recorded for this Sales Order"))
