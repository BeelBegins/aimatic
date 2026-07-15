import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RestaurantMenuItem(Document):
	def validate(self):
		item = frappe.get_cached_doc("Item", self.item)
		if item.disabled or not item.is_sales_item:
			frappe.throw(_("Restaurant Menu Item must reference an enabled sales Item"))
		if cint(self.preparation_minutes) < 0:
			frappe.throw(_("Preparation minutes cannot be negative"))
		groups = [row.modifier_group for row in self.modifier_groups]
		if len(groups) != len(set(groups)):
			frappe.throw(_("A modifier group can be linked only once"))
