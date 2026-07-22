import frappe
from frappe import _
from frappe.model.document import Document


class MobileSalesAssortment(Document):
	def validate(self):
		if bool(self.item) == bool(self.item_group):
			frappe.throw(_("Select either Item or Item Group, but not both"))
		filters = {"customer": self.customer, "name": ["!=", self.name]}
		filters["item" if self.item else "item_group"] = self.item or self.item_group
		if frappe.db.exists("Mobile Sales Assortment", filters):
			frappe.throw(_("This customer assortment rule already exists"))
