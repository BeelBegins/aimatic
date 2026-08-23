import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MobileSalesDiscountAuthority(Document):
	def validate(self):
		maximum = flt(self.maximum_discount_percent)
		if maximum < 0 or maximum > 100:
			frappe.throw(_("Maximum Discount Percent must be between 0 and 100"))
		if frappe.db.exists(
			"Mobile Sales Discount Authority",
			{"user": self.user, "name": ["!=", self.name]},
		):
			frappe.throw(_("Discount authority is already configured for this user"))
