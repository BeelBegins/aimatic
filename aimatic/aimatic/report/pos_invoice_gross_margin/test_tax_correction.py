import unittest

from aimatic.aimatic.report.pos_invoice_gross_margin.pos_invoice_gross_margin import _set_margin_values


def _row(**kw):
	base = dict(
		qty=1,
		stock_qty=1,
		sales=100,
		gross_amount=118,
		sales_tax=18,
		tax_rate=18,
		tax_invalid=0,
		direct_cogs=80,
		is_return=0,
		is_stock_item=1,
		consolidated_invoice="SINV-1",
		consolidated_item="x",
	)
	base.update(kw)
	return base


class TestTaxCorrection(unittest.TestCase):
	def test_valid_invoice_is_untouched(self):
		row = _set_margin_values(_row(sales=97.5), 2)
		self.assertEqual(row.sales, 97.5)
		self.assertFalse(row.get("tax_adjusted"))

	def test_invalid_invoice_uses_amount_minus_line_tax(self):
		# a good line on an invoice whose blended rate was destroyed by another line
		row = _set_margin_values(_row(sales=1.26, gross_amount=449, sales_tax=68.49, tax_invalid=1), 2)
		self.assertEqual(row.sales, 380.51)
		self.assertEqual(row.sales_incl_tax, 449)
		self.assertEqual(row.tax_adjusted, 1)

	def test_offending_line_tax_is_recomputed(self):
		# 28 Box at Rs 99 charged 2,772 but tax snapshot 11,959.32
		row = _set_margin_values(
			_row(sales=35.16, gross_amount=2772, sales_tax=11959.32, tax_invalid=1, direct_cogs=58619), 2
		)
		self.assertEqual(row.sales_tax, 422.85)
		self.assertEqual(row.sales, 2349.15)
		self.assertEqual(row.sales_incl_tax, 2772)

	def test_return_rows_correct_with_sign(self):
		row = _set_margin_values(
			_row(
				sales=-1, gross_amount=-2772, sales_tax=-11959.32, tax_invalid=1, is_return=1, direct_cogs=-5
			),
			2,
		)
		self.assertEqual(row.sales_tax, -422.85)
		self.assertEqual(row.sales, -2349.15)


if __name__ == "__main__":
	unittest.main()
