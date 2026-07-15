import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class RestaurantTable(Document):
	def validate(self):
		floor = frappe.get_cached_doc("Restaurant Floor", self.floor)
		if floor.branch != self.branch:
			frappe.throw(_("Table Branch must match its Floor"))
		if floor.disabled:
			frappe.throw(_("Cannot use a disabled Restaurant Floor"))
		if cint(self.capacity) < 1:
			frappe.throw(_("Table capacity must be at least one"))
		self.title = (self.title or "").strip()
		duplicate = frappe.db.exists("Restaurant Table", {"name": ["!=", self.name], "floor": self.floor, "title": self.title})
		if duplicate:
			frappe.throw(_("Table {0} already exists on this Floor").format(self.title))
