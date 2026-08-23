import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime

from aimatic.label_printing.utils import compute_job_totals, resolve_final_date


class AIMLabelPrintJob(Document):
	def validate(self):
		self.validate_template()
		self.resolve_dates_and_defaults()
		self.validate_items()
		self.apply_totals()
		self.set_meta()

	def validate_template(self):
		if not self.template:
			return

		template = frappe.db.get_value(
			"AIM Label Template",
			self.template,
			["enabled", "label_type"],
			as_dict=True,
		)
		if not template:
			frappe.throw(_("Label Template {0} does not exist.").format(self.template))

		if not template.enabled:
			frappe.throw(_("Label Template {0} is disabled.").format(self.template))

		if template.label_type != self.label_type:
			frappe.throw(
				_("Label Template {0} is a {1} template and cannot be used for a {2} job.").format(
					self.template, template.label_type, self.label_type
				)
			)

	def resolve_dates_and_defaults(self):
		for row in self.items:
			if row.manual_mfg_date and row.manual_expiry_date:
				if getdate(row.manual_expiry_date) < getdate(row.manual_mfg_date):
					frappe.throw(
						_("Row #{0}: Manual Expiry Date cannot be before Manual MFG Date.").format(row.idx)
					)

			row.final_mfg_date = resolve_final_date(row.manual_mfg_date, row.system_mfg_date)
			row.final_expiry_date = resolve_final_date(row.manual_expiry_date, row.system_expiry_date)

			if row.final_mfg_date and row.final_expiry_date:
				if getdate(row.final_expiry_date) < getdate(row.final_mfg_date):
					frappe.throw(_("Row #{0}: Expiry Date cannot be before MFG Date.").format(row.idx))

	def validate_items(self):
		for row in self.items:
			if not row.item_code:
				frappe.throw(_("Row #{0}: Item Code is required.").format(row.idx))

			if self.label_type == "Shelf Label":
				row.shelf_label_qty = 1
				row.barcode_label_qty = 1
			else:
				if row.print_label and flt(row.barcode_label_qty) <= 0:
					frappe.throw(_("Row #{0}: Barcode Label Qty must be greater than 0.").format(row.idx))
				row.shelf_label_qty = 0

			row.remarks = "" if row.barcode else _("No barcode found")

	def apply_totals(self):
		totals = compute_job_totals(self)
		self.total_item_rows = totals["total_item_rows"]
		self.total_barcode_labels = totals["total_barcode_labels"]
		self.total_shelf_labels = totals["total_shelf_labels"]
		self.missing_barcode_count = totals["missing_barcode_count"]

		if self.total_item_rows and self.status == "Draft":
			self.status = "Ready"

	def set_meta(self):
		if not self.generated_by:
			self.generated_by = frappe.session.user
		if not self.generated_on:
			self.generated_on = now_datetime()

	def before_print_check(self):
		"""Raise if the job cannot be printed as-is. Called by print/preview
		and TSPL generation entry points, not on every save."""
		if not self.template:
			frappe.throw(_("Select a Label Template before printing."))

		if not self.items:
			frappe.throw(_("Add at least one item before printing."))

		printable_rows = [row for row in self.items if row.print_label]
		if not printable_rows:
			frappe.throw(_("No rows are marked for printing."))

		if self.label_type == "Barcode Label":
			missing = [row.item_code for row in printable_rows if not row.barcode]
			if missing:
				frappe.throw(
					_("These items have no barcode and cannot be printed: {0}").format(", ".join(missing))
				)
