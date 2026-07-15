import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RestaurantModifierGroup(Document):
	def validate(self):
		codes = []
		for row in self.options:
			row.option_code = (row.option_code or "").strip()
			row.label = (row.label or "").strip()
			codes.append(row.option_code.lower())
		if len(codes) != len(set(codes)):
			frappe.throw(_("Modifier option codes must be unique within a group"))
		if cint(self.minimum_selections) < 0 or cint(self.maximum_selections) < 1:
			frappe.throw(_("Modifier selection limits are invalid"))
		if cint(self.minimum_selections) > cint(self.maximum_selections):
			frappe.throw(_("Minimum selections cannot exceed maximum selections"))
