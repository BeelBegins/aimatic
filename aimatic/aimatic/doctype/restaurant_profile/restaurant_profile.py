import frappe
from frappe import _
from frappe.model.document import Document


class RestaurantProfile(Document):
	def validate(self):
		pos = frappe.get_cached_doc("POS Profile", self.pos_profile)
		branch = frappe.get_cached_doc("Branch", self.branch)
		if pos.get("disabled"):
			frappe.throw(_("POS Profile {0} is disabled").format(pos.name))
		if branch.company != self.company:
			frappe.throw(_("Restaurant Profile company must match its Branch"))
		if self.company != pos.company:
			frappe.throw(_("Restaurant Profile company must match its POS Profile"))
		if self.branch and pos.get("branch") and self.branch != pos.branch:
			frappe.throw(_("Restaurant Profile branch must match its POS Profile"))
		if self.default_customer and frappe.db.get_value("Customer", self.default_customer, "disabled"):
			frappe.throw(_("Default Customer is disabled"))
		if self.warehouse:
			warehouse = frappe.db.get_value("Warehouse", self.warehouse, ["company", "disabled", "is_group"], as_dict=True)
			if not warehouse or warehouse.company != self.company or warehouse.disabled or warehouse.is_group:
				frappe.throw(_("Restaurant Warehouse must be an active stock warehouse for this Company"))
		if self.menu_price_list and not frappe.db.get_value("Price List", self.menu_price_list, "selling"):
			frappe.throw(_("Restaurant Menu Price List must be a selling Price List"))
