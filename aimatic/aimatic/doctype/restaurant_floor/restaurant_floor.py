import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RestaurantFloor(Document):
	def validate(self):
		self.title = (self.title or "").strip()
		if not self.title:
			frappe.throw(_("Floor title is required"))
		if cint(self.display_order) < 0:
			frappe.throw(_("Display order cannot be negative"))
		duplicate = frappe.db.exists(
			"Restaurant Floor", {"name": ["!=", self.name], "branch": self.branch, "title": self.title}
		)
		if duplicate:
			frappe.throw(_("Floor {0} already exists for this Branch").format(self.title))
