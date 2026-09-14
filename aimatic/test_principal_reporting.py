from __future__ import annotations

import unittest

import frappe

from aimatic.principal_reporting import UNCLASSIFIED_PRINCIPAL, split_by_principal


class TestPrincipalReporting(unittest.TestCase):
	def test_unallocated_row_keeps_total(self):
		row = frappe._dict(principal="", invoiced=100, paid=20, outstanding=80)
		result = split_by_principal(row, [], 100, ("invoiced", "paid", "outstanding"))
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0].principal, UNCLASSIFIED_PRINCIPAL)
		self.assertEqual(result[0].outstanding, 80)

	def test_mixed_invoice_is_split_proportionally(self):
		row = frappe._dict(invoiced=100, paid=25, outstanding=75)
		allocations = [
			frappe._dict(principal="ABBOTT", allocated_amount=60),
			frappe._dict(principal="SHIELD", allocated_amount=40),
		]
		result = split_by_principal(row, allocations, 100, ("invoiced", "paid", "outstanding"))
		self.assertEqual([entry.principal for entry in result], ["ABBOTT", "SHIELD"])
		self.assertAlmostEqual(sum(entry.invoiced for entry in result), 100)
		self.assertAlmostEqual(sum(entry.paid for entry in result), 25)
		self.assertAlmostEqual(sum(entry.outstanding for entry in result), 75)


if __name__ == "__main__":
	unittest.main()
