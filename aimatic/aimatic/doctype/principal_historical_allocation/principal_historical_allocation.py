from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

SOURCE_META = {
	"Purchase Invoice": ("posting_date", "rounded_total"),
	"Purchase Receipt": ("posting_date", "rounded_total"),
	"Purchase Order": ("transaction_date", "rounded_total"),
}
SOURCE_ITEMS = {
	"Purchase Invoice": "Purchase Invoice Item",
	"Purchase Receipt": "Purchase Receipt Item",
	"Purchase Order": "Purchase Order Item",
}


class PrincipalHistoricalAllocation(Document):
	def validate(self):
		self._load_source_document()
		self._validate_allocations()

	def _load_source_document(self):
		if self.voucher_type not in SOURCE_META:
			frappe.throw(_("Unsupported voucher type {0}").format(frappe.bold(self.voucher_type)))
		source = frappe.get_doc(self.voucher_type, self.voucher_no)
		if source.docstatus != 1:
			frappe.throw(_("Historical allocation is allowed only for submitted vouchers."))
		date_field, total_field = SOURCE_META[self.voucher_type]
		self.company = source.company
		self.supplier = source.supplier
		self.posting_date = source.get(date_field)
		self.document_principal = source.get("custom_principal")
		self.document_total = source.get(total_field) or source.get("grand_total") or 0

	def _validate_allocations(self):
		if not self.allocations:
			frappe.throw(_("Add at least one Principal allocation."))
		existing = frappe.db.exists(
			"Principal Historical Allocation",
			{
				"voucher_type": self.voucher_type,
				"voucher_no": self.voucher_no,
				"docstatus": 1,
				"name": ["!=", self.name or ""],
			},
		)
		if existing:
			frappe.throw(
				_("Submitted historical allocation {0} already exists for this voucher.").format(
					frappe.bold(existing)
				)
			)
		allowed = set(
			frappe.get_all(
				"Supplier Principal",
				filters={"parent": self.supplier, "parenttype": "Supplier"},
				pluck="principal",
			)
		)
		row_doctype = SOURCE_ITEMS[self.voucher_type]
		for row in self.allocations:
			if allowed and row.principal not in allowed:
				frappe.throw(
					_("Row {0}: Principal {1} is not allowed for supplier {2}.").format(
						row.idx, frappe.bold(row.principal), frappe.bold(self.supplier)
					)
				)
			if row.source_row:
				source_row = frappe.db.get_value(
					row_doctype, row.source_row, ["parent", "item_code"], as_dict=True
				)
				if not source_row or source_row.parent != self.voucher_no:
					frappe.throw(_("Row {0}: source row does not belong to this voucher.").format(row.idx))
				if row.item_code and source_row.item_code != row.item_code:
					frappe.throw(_("Row {0}: Item does not match the source row.").format(row.idx))
				row.item_code = source_row.item_code

		self.allocated_total = sum(flt(row.allocated_amount) for row in self.allocations)
		precision = self.precision("allocated_total") or 2
		if abs(flt(self.allocated_total, precision) - flt(self.document_total, precision)) > 10 ** (
			-precision
		):
			frappe.throw(
				_("Allocated total {0} must equal document total {1}.").format(
					frappe.format_value(self.allocated_total, {"fieldtype": "Currency"}),
					frappe.format_value(self.document_total, {"fieldtype": "Currency"}),
				)
			)
