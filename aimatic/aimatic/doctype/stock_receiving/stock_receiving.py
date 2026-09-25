from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime

from aimatic.branch_management.utils import get_user_default_branch, user_can_override


class StockReceiving(Document):
	def validate(self):
		if self.source_type not in {"Stock Entry", "Purchase Receipt"}:
			frappe.throw(_("Only Stock Entry and Purchase Receipt receiving is supported."))
		if not self.source_name:
			frappe.throw(_("A source document is required."))
		if not self.items:
			frappe.throw(_("At least one item is required."))
		if self.status not in {"Received", "Disputed"}:
			frappe.throw(_("A receiving record must be Received or Disputed before submission."))
		if not user_can_override() and self.receiving_branch != get_user_default_branch():
			frappe.throw(_("Receiving branch must match your assigned Branch."), frappe.PermissionError)
		for row in self.items:
			if flt(row.expected_qty) < 0 or flt(row.received_qty) < 0:
				frappe.throw(_("Row {0}: quantities cannot be negative.").format(row.idx))
			if flt(row.received_qty) > flt(row.expected_qty) and self.source_type == "Stock Entry":
				frappe.throw(_("Row {0}: received quantity cannot exceed the transferred quantity.").format(row.idx))

	def before_submit(self):
		if not self.receiver:
			self.receiver = frappe.session.user
		if not self.received_at:
			self.received_at = now_datetime()
