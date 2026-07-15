import frappe
from frappe import _
from frappe.model.document import Document


TICKET_TRANSITIONS = {"Queued": {"Queued", "Preparing", "Cancelled"}, "Preparing": {"Preparing", "Ready", "Cancelled"}, "Ready": {"Ready", "Served"}, "Served": {"Served"}, "Cancelled": {"Cancelled"}}


class RestaurantKitchenTicket(Document):
	def validate(self):
		order = frappe.get_cached_doc("Restaurant Order", self.restaurant_order)
		if order.branch != self.branch or order.restaurant_table != self.restaurant_table:
			frappe.throw(_("Kitchen Ticket context must match its Restaurant Order"))
		before = self.get_doc_before_save()
		if before and self.status not in TICKET_TRANSITIONS.get(before.status, set()):
			frappe.throw(_("Kitchen Ticket cannot move from {0} to {1}").format(before.status, self.status))
		if before:
			before_rows = [(x.order_item_row, x.item, x.qty, x.notes, x.modifiers_json) for x in before.items]
			rows = [(x.order_item_row, x.item, x.qty, x.notes, x.modifiers_json) for x in self.items]
			if rows != before_rows:
				frappe.throw(_("Kitchen Ticket items are immutable after creation"))
