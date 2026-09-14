from __future__ import annotations

from collections import defaultdict

import frappe
from frappe.utils import flt

UNALLOCATED_PRINCIPAL = "Unallocated / Legal Supplier"
UNCLASSIFIED_PRINCIPAL = "Unclassified"


def get_approved_allocation_shares(voucher_names):
	"""Return submitted historical allocation shares by source voucher."""
	if not voucher_names:
		return {}
	rows = frappe.db.sql(
		"""
		select pha.voucher_no, phai.principal, sum(phai.allocated_amount) allocated_amount
		from `tabPrincipal Historical Allocation` pha
		join `tabPrincipal Historical Allocation Item` phai on phai.parent = pha.name
		where pha.docstatus = 1 and pha.voucher_no in %(voucher_names)s
		group by pha.voucher_no, phai.principal
		""",
		{"voucher_names": tuple(voucher_names)},
		as_dict=True,
	)
	grouped = defaultdict(list)
	for row in rows:
		grouped[row.voucher_no].append(row)
	return dict(grouped)


def split_by_principal(row, allocations, total, numeric_fields):
	"""Split one report row proportionally without changing its total."""
	if not allocations:
		copy = frappe._dict(row.copy())
		copy.principal = row.get("principal") or UNCLASSIFIED_PRINCIPAL
		return [copy]
	denominator = flt(total)
	if not denominator:
		denominator = sum(flt(entry.allocated_amount) for entry in allocations)
	result = []
	allocated_numeric = defaultdict(float)
	for index, entry in enumerate(allocations):
		copy = frappe._dict(row.copy())
		copy.principal = entry.principal
		ratio = flt(entry.allocated_amount) / denominator if denominator else 0
		for fieldname in numeric_fields:
			if index == len(allocations) - 1:
				copy[fieldname] = flt(row.get(fieldname)) - allocated_numeric[fieldname]
			else:
				copy[fieldname] = flt(copy.get(fieldname)) * ratio
				allocated_numeric[fieldname] += copy[fieldname]
		result.append(copy)
	return result
