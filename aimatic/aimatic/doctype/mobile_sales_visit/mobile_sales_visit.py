import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class MobileSalesVisit(Document):
	def validate(self):
		if self.warehouse:
			warehouse = frappe.db.get_value("Warehouse", self.warehouse, ["company", "disabled", "is_group"], as_dict=True)
			if not warehouse or warehouse.company != self.company or warehouse.disabled or warehouse.is_group:
				frappe.throw(_("Visit Warehouse must be an active stock warehouse for the selected Company"))
		user = frappe.db.get_value("User", self.assigned_to, ["enabled", "user_type"], as_dict=True)
		if not user or not user.enabled or user.user_type != "System User":
			frappe.throw(_("Assigned Sales User must be an enabled system user"))
		roles = set(frappe.get_roles(self.assigned_to))
		if not {"Sales User", "Sales Manager", "System Manager"}.intersection(roles):
			frappe.throw(_("Assigned user must have a Sales User or Sales Manager role"))
		if self.customer_address and not frappe.db.exists(
			"Dynamic Link",
			{
				"parenttype": "Address",
				"parent": self.customer_address,
				"link_doctype": "Customer",
				"link_name": self.customer,
			},
		):
			frappe.throw(_("Visit Address must be linked to the selected Customer"))
		for prefix in ("planned", "check_in", "check_out"):
			latitude = self.get(f"{prefix}_latitude")
			longitude = self.get(f"{prefix}_longitude")
			if latitude in (None, "") and longitude in (None, ""):
				continue
			if latitude in (None, "") or longitude in (None, ""):
				frappe.throw(_("Both latitude and longitude are required"))
			if not -90 <= flt(latitude) <= 90 or not -180 <= flt(longitude) <= 180:
				frappe.throw(_("Visit coordinates are outside the valid latitude/longitude range"))
		if self.status in {"Checked In", "Completed"} and (
			not self.check_in_at or self.check_in_latitude in (None, "") or self.check_in_longitude in (None, "")
		):
			frappe.throw(_("Checked-in visits require a check-in time and GPS location"))
		if self.status == "Completed" and (
			not self.check_out_at or self.check_out_latitude in (None, "") or self.check_out_longitude in (None, "")
		):
			frappe.throw(_("Completed visits require a check-out time and GPS location"))
