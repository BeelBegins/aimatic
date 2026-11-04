import frappe
from frappe import _
from frappe.model.document import Document


class POSVoidLog(Document):
	"""A cart void reported by a POS terminal: a removed line, a cleared cart or a
	deleted held sale. Written only by aimatic.offline_pos.api.log_pos_voids; the
	record is audit evidence, so only System Manager may change or delete it."""

	def validate(self):
		if not self.is_new() and "System Manager" not in frappe.get_roles():
			frappe.throw(_("POS Void Log records cannot be edited"), frappe.PermissionError)

	def on_trash(self):
		if "System Manager" not in frappe.get_roles():
			frappe.throw(_("POS Void Log records cannot be deleted"), frappe.PermissionError)
