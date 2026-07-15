import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


ACTIVE_STATUSES = ("Open", "Sent to Kitchen", "Bill Requested")
ALLOWED_TRANSITIONS = {
	"Open": {"Open", "Sent to Kitchen", "Cancelled"},
	"Sent to Kitchen": {"Sent to Kitchen", "Bill Requested", "Cancelled"},
	"Bill Requested": {"Bill Requested", "Closed"},
	"Closed": {"Closed"},
	"Cancelled": {"Cancelled"},
}


class RestaurantOrder(Document):
	def validate(self):
		self._validate_context()
		self._validate_transition()
		self._validate_items()
		self._validate_active_table()

	def _validate_context(self):
		table = frappe.get_cached_doc("Restaurant Table", self.restaurant_table)
		if table.floor != self.floor or table.branch != self.branch:
			frappe.throw(_("Order Floor and Branch must match the Restaurant Table"))
		profile = frappe.get_cached_doc("Restaurant Profile", self.restaurant_profile)
		if profile.branch and profile.branch != self.branch:
			frappe.throw(_("Order Branch must match the Restaurant Profile"))
		if profile.pos_profile != self.pos_profile or profile.company != self.company:
			frappe.throw(_("Order POS Profile and Company must match the Restaurant Profile"))
		if flt(self.guest_count) <= 0:
			frappe.throw(_("Guest count must be greater than zero"))

	def _validate_transition(self):
		before = self.get_doc_before_save()
		if before and self.status not in ALLOWED_TRANSITIONS.get(before.status, set()):
			frappe.throw(_("Restaurant Order cannot move from {0} to {1}").format(before.status, self.status))

	def _validate_items(self):
		total = 0
		for row in self.items:
			if flt(row.qty) <= 0 or flt(row.sent_qty) < 0 or flt(row.sent_qty) > flt(row.qty):
				frappe.throw(_("Restaurant item quantities are invalid"))
			row.amount = flt(row.qty) * flt(row.rate)
			total += row.amount
		self.net_total = total
		self.grand_total = total + flt(self.total_taxes_and_charges)

	def _validate_active_table(self):
		if self.status not in ACTIVE_STATUSES:
			return
		duplicate = frappe.db.exists(
			"Restaurant Order",
			{"name": ["!=", self.name], "restaurant_table": self.restaurant_table, "status": ["in", ACTIVE_STATUSES]},
		)
		if duplicate:
			frappe.throw(_("This table already has an active Restaurant Order"))
