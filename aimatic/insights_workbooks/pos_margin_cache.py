"""Bounded refresh of ledger-covered POS receipts missing from the Insights cache."""

import frappe
from redis.exceptions import LockError

BATCH_SIZE = 200
LOCK_NAME = "aimatic:pos-margin-cache-refresh"

# Keep the established correction-date, tax normalization and ledger-COGS rules.
SOURCE_SQL = "\nWITH line_margin AS (\n\tSELECT\n\t\tDATE(COALESCE(\n\t\t\t(\n\t\t\t\tSELECT corrected.posting_date\n\t\t\t\tFROM `tabPOS Invoice` corrected\n\t\t\t\tWHERE corrected.name = CASE\n\t\t\t\t\tWHEN pi.custom_terminal_refund_id LIKE 'server-correction-%' THEN pi.return_against\n\t\t\t\t\tWHEN pi.custom_terminal_invoice_id LIKE 'server-correction-%'\n\t\t\t\t\t\tTHEN SUBSTRING(pi.custom_terminal_invoice_id, LOCATE('ACC-PSINV-', pi.custom_terminal_invoice_id))\n\t\t\t\tEND\n\t\t\t),\n\t\t\tpi.posting_date\n\t\t)) AS posting_date,\n\t\tpi.company,\n\t\tCOALESCE(NULLIF(pi.branch, ''), pp.branch, 'Unassigned') AS branch,\n\t\tpi.name AS pos_invoice,\n\t\tpi.grand_total AS sales_incl_taxes,\n\t\tpii.base_net_amount AS raw_sales,\n\t\tpii.amount AS gross_amount,\n\t\tpii.custom_fbr_sales_tax AS sales_tax,\n\t\tpii.custom_fbr_tax_rate AS tax_rate,\n\t\tEXISTS (\n\t\t\tSELECT 1\n\t\t\tFROM `tabPOS Invoice Item` bad\n\t\t\tWHERE bad.parent = pi.name\n\t\t\t\tAND bad.parenttype = 'POS Invoice'\n\t\t\t\tAND bad.amount != 0\n\t\t\t\tAND bad.custom_fbr_sales_tax != 0\n\t\t\t\tAND ABS(bad.custom_fbr_sales_tax) >= ABS(bad.amount)\n\t\t) AS tax_invalid,\n\t\t(\n\t\t\tSELECT -SUM(sle.stock_value_difference)\n\t\t\tFROM `tabStock Ledger Entry` sle\n\t\t\tWHERE sle.voucher_type = 'Sales Invoice'\n\t\t\t\tAND sle.voucher_no = sii.parent\n\t\t\t\tAND sle.voucher_detail_no = sii.name\n\t\t\t\tAND sle.is_cancelled = 0\n\t\t) AS cogs\n\tFROM `tabPOS Invoice` pi\n\tINNER JOIN `tabPOS Invoice Item` pii\n\t\tON pii.parent = pi.name AND pii.parenttype = 'POS Invoice'\n\tLEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile\n\tLEFT JOIN `tabSales Invoice Item` sii\n\t\tON sii.parent = pi.consolidated_invoice\n\t\tAND sii.pos_invoice = pi.name\n\t\tAND sii.pos_invoice_item = pii.name\n\t\tAND sii.docstatus = 1\n\tWHERE pi.docstatus = 1\n), normalized AS (\n\tSELECT\n\t\tposting_date,\n\t\tcompany,\n\t\tbranch,\n\t\tpos_invoice,\n\t\tsales_incl_taxes,\n\t\tROUND(\n\t\t\tCASE\n\t\t\t\tWHEN tax_invalid = 1 THEN gross_amount - CASE\n\t\t\t\t\tWHEN ABS(sales_tax) >= ABS(gross_amount) AND tax_rate > 0\n\t\t\t\t\t\tTHEN gross_amount * tax_rate / (100 + tax_rate)\n\t\t\t\t\tWHEN ABS(sales_tax) >= ABS(gross_amount) THEN 0\n\t\t\t\t\tELSE sales_tax\n\t\t\t\tEND\n\t\t\t\tELSE raw_sales\n\t\t\tEND,\n\t\t\t2\n\t\t) AS sales,\n\t\tROUND(cogs, 2) AS cogs\n\tFROM line_margin\n\tWHERE cogs IS NOT NULL\n)\nSELECT\n\tposting_date,\n\tDATE_SUB(posting_date, INTERVAL DAYOFMONTH(posting_date) - 1 DAY) AS month_start,\n\tDAYNAME(posting_date) AS day_name,\n\tcompany,\n\tbranch,\n\tpos_invoice,\n\tROUND(MAX(sales_incl_taxes), 2) AS sales_incl_taxes,\n\tROUND(SUM(sales), 2) AS sales,\n\tROUND(SUM(cogs), 2) AS cogs,\n\tROUND(SUM(sales) - SUM(cogs), 2) AS gross_margin\nFROM normalized\nGROUP BY posting_date, company, branch, pos_invoice\n"

CANDIDATE_SQL = "select pi.name,pi.posting_date from `tabPOS Invoice` pi where pi.docstatus=1 and pi.consolidated_invoice is not null and pi.consolidated_invoice!='' and not exists(select 1 from `tabInsights POS Margin Daily` c where c.name=pi.name) and exists(select 1 from `tabSales Invoice Item` sii where sii.parent=pi.consolidated_invoice and sii.pos_invoice=pi.name and sii.docstatus=1 and exists(select 1 from `tabStock Ledger Entry` sle where sle.voucher_type='Sales Invoice' and sle.voucher_no=sii.parent and sle.voucher_detail_no=sii.name and sle.is_cancelled=0)) order by pi.posting_date,pi.name limit %(batch_size)s"


def get_missing_invoices():
	return frappe.db.sql(CANDIDATE_SQL, {"batch_size": BATCH_SIZE}, pluck=True)


def get_margin_rows(invoices):
	if not invoices:
		return []
	query = SOURCE_SQL.replace("%", "%%").replace(
		"WHERE pi.docstatus = 1\n)",
		"WHERE pi.docstatus = 1 AND pi.name IN %(invoices)s\n)",
		1,
	)
	return frappe.db.sql(query, {"invoices": tuple(invoices)}, as_dict=True)


@frappe.whitelist(methods=["POST"])
def refresh_missing():
	"""Standalone scheduled/manual cache batch; never called inside a sale transaction."""
	frappe.only_for("System Manager")
	if not frappe.db.exists("DocType", "Insights POS Margin Daily"):
		return {"upserted": 0}
	try:
		with frappe.cache.lock(LOCK_NAME, timeout=300, blocking_timeout=0):
			invoices = get_missing_invoices()
			if not invoices:
				return {"upserted": 0}
			rows = get_margin_rows(invoices)
			if set(invoices) != {row.pos_invoice for row in rows}:
				frappe.throw("POS margin cache candidates do not match their ledger-covered receipt lines.")
			try:
				for row in rows:
					row["name"] = row["pos_invoice"]
					row["doctype"] = "Insights POS Margin Daily"
					frappe.get_doc(row).insert(ignore_permissions=True)
				# Finish this standalone cache transaction before releasing the lock,
				# so another scheduler/manual batch cannot select the same receipts.
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				raise
			return {"upserted": len(rows)}
	except LockError:
		return {"upserted": 0, "busy": True}
