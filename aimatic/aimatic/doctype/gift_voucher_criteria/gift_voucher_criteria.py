import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class GiftVoucherCriteria(Document):
	def validate(self):
		if flt(self.min_value) >= flt(self.max_value):
			frappe.throw(_("Start Value must be less than End Value"))

		if not (0 < flt(self.percentage) <= 100):
			frappe.throw(_("Voucher Percentage must be between 0 and 100"))

		if flt(self.validity_days) <= 0:
			frappe.throw(_("Validity (Days) must be greater than 0"))

		if flt(self.minimum_redemption_value) < 0:
			frappe.throw(_("Minimum Redemption Value cannot be negative"))
