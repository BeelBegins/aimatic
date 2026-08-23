import frappe
from frappe import _
from frappe.model.document import Document


class AIMLabelTemplate(Document):
	def validate(self):
		if self.paper_type == "Roll":
			if not self.label_width_mm or self.label_width_mm <= 0:
				frappe.throw(_("Label Width (mm) must be greater than 0 for Roll labels."))
			if not self.label_height_mm or self.label_height_mm <= 0:
				frappe.throw(_("Label Height (mm) must be greater than 0 for Roll labels."))
		else:
			if not self.page_width_mm or self.page_width_mm <= 0:
				frappe.throw(_("Page Width (mm) must be greater than 0 for A4 labels."))
			if not self.page_height_mm or self.page_height_mm <= 0:
				frappe.throw(_("Page Height (mm) must be greater than 0 for A4 labels."))

		if not self.column_count or self.column_count <= 0:
			self.column_count = 1

		if self.label_type == "Shelf Label" and self.paper_type != "A4":
			frappe.msgprint(
				_("Shelf Label templates are normally printed on A4 paper."),
				indicator="orange",
				alert=True,
			)
