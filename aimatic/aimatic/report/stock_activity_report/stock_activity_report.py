from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

SOLD_VOUCHER_TYPES = ("Sales Invoice", "POS Invoice", "Delivery Note")
ADJUSTMENT_VOUCHER_TYPES = ("Stock Reconciliation",)


def _sql_in(values: tuple[str, ...]) -> str:
	"""Tuple -> SQL IN-list literal. Values are hardcoded module constants
	only, never user input - plain repr() would emit an invalid trailing
	comma for a 1-tuple (e.g. `('Stock Reconciliation',)`)."""
	return "(" + ", ".join(f"'{v}'" for v in values) + ")"

# Rows above this are refused up front rather than risking a slow/expensive
# full-history scan - narrow the filters instead (company/warehouse/item).
MAX_SCOPED_ROWS = 300_000


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)

	warehouse_restriction = _get_warehouse_restriction()
	if warehouse_restriction is not None and not warehouse_restriction:
		return get_columns(), [], _("No warehouse is visible to your account."), None, None

	row_count = count_scoped_rows(filters, warehouse_restriction)
	if row_count > MAX_SCOPED_ROWS:
		frappe.throw(
			_(
				"This date range and filter combination covers {0} stock ledger rows, which is too "
				"many to scan at once. Narrow it with Company, Warehouse, Branch, or Item first."
			).format(row_count)
		)

	currency = frappe.get_cached_value("Company", filters.company, "default_currency") if filters.company else None
	precision = cint(frappe.db.get_default("currency_precision") or 2)

	rows = get_rows(filters, warehouse_restriction)
	data = [_set_row_values(row, precision) for row in rows]
	for row in data:
		row.currency = currency

	return get_columns(currency), data, get_message(), None, get_report_summary(data, currency, precision)


def validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are mandatory"))
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date must be before To Date"))


def _get_warehouse_restriction() -> list[str] | None:
	"""None = unrestricted. A list (possibly empty) = only these warehouses,
	derived from the user's Branch User Permission(s) via Warehouse.custom_branch
	(Stock Ledger Entry itself carries no branch field to filter on directly)."""

	permissions = frappe.permissions.get_user_permissions(frappe.session.user)
	branch_perms = permissions.get("Branch")
	if not branch_perms:
		return None

	branches = [p.doc for p in branch_perms]
	return frappe.get_all(
		"Warehouse", filters={"custom_branch": ["in", branches], "disabled": 0}, pluck="name"
	)


def _build_conditions(filters, warehouse_restriction):
	conditions = [
		"sle.`is_cancelled` = 0",
		"sle.`docstatus` < 2",
		"sle.`company` = %(company)s",
		"sle.`posting_date` <= %(to_date)s",
	]

	for fieldname, sql_field in (("warehouse", "sle.`warehouse`"), ("item_code", "sle.`item_code`")):
		if filters.get(fieldname):
			conditions.append(f"{sql_field} = %({fieldname})s")

	if filters.get("branch"):
		conditions.append("wh.`custom_branch` = %(branch)s")

	if warehouse_restriction is not None:
		conditions.append("sle.`warehouse` IN %(warehouse_restriction)s")
		filters["warehouse_restriction"] = warehouse_restriction

	return conditions


def count_scoped_rows(filters, warehouse_restriction):
	conditions = _build_conditions(filters, warehouse_restriction)
	where_clause = " AND ".join(conditions)

	# nosemgrep
	return frappe.db.sql(
		f"""
		SELECT COUNT(*)
		FROM `tabStock Ledger Entry` sle
		LEFT JOIN `tabWarehouse` wh ON wh.`name` = sle.`warehouse`
		WHERE {where_clause}
		""",
		filters,
	)[0][0]


def get_rows(filters, warehouse_restriction):
	conditions = _build_conditions(filters, warehouse_restriction)
	where_clause = " AND ".join(conditions)

	# nosemgrep
	return frappe.db.sql(
		f"""
		WITH scoped_raw AS (
			SELECT
				sle.`item_code`,
				sle.`warehouse`,
				sle.`posting_date`,
				sle.`posting_datetime`,
				sle.`creation`,
				sle.`voucher_type`,
				sle.`qty_after_transaction`,
				sle.`stock_value`,
				sle.`stock_value_difference`,
				item.`item_name`,
				item.`item_group`,
				item.`stock_uom`,
				wh.`custom_branch` AS `branch`
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabItem` item ON item.`name` = sle.`item_code`
			LEFT JOIN `tabWarehouse` wh ON wh.`name` = sle.`warehouse`
			WHERE {where_clause}
		),
		scoped AS (
			SELECT
				*,
				`qty_after_transaction` - COALESCE(LAG(`qty_after_transaction`, 1) OVER (
					PARTITION BY `item_code`, `warehouse`
					ORDER BY `posting_datetime`, `creation`
				), 0) AS `qty_delta`
			FROM scoped_raw
		),
		opening AS (
			SELECT `item_code`, `warehouse`, `qty_after_transaction` AS opening_qty, `stock_value` AS opening_value
			FROM (
				SELECT
					`item_code`, `warehouse`, `qty_after_transaction`, `stock_value`,
					ROW_NUMBER() OVER (
						PARTITION BY `item_code`, `warehouse`
						ORDER BY `posting_datetime` DESC, `creation` DESC
					) AS rn
				FROM scoped
				WHERE `posting_date` < %(from_date)s
			) ranked
			WHERE rn = 1
		),
		movement AS (
			SELECT
				`item_code`, `warehouse`, `item_name`, `item_group`, `stock_uom`, `branch`,
				SUM(CASE WHEN `voucher_type` IN {_sql_in(SOLD_VOUCHER_TYPES)} THEN `qty_delta` ELSE 0 END) AS sold_qty,
				SUM(CASE WHEN `voucher_type` IN {_sql_in(SOLD_VOUCHER_TYPES)} THEN `stock_value_difference` ELSE 0 END) AS sold_value,
				SUM(CASE WHEN `voucher_type` IN {_sql_in(ADJUSTMENT_VOUCHER_TYPES)} THEN `qty_delta` ELSE 0 END) AS adjustment_qty,
				SUM(CASE WHEN `voucher_type` IN {_sql_in(ADJUSTMENT_VOUCHER_TYPES)} THEN `stock_value_difference` ELSE 0 END) AS adjustment_value,
				SUM(
					CASE WHEN `voucher_type` NOT IN {_sql_in(SOLD_VOUCHER_TYPES + ADJUSTMENT_VOUCHER_TYPES)}
						AND `qty_delta` > 0 THEN `qty_delta` ELSE 0 END
				) AS in_qty,
				SUM(
					CASE WHEN `voucher_type` NOT IN {_sql_in(SOLD_VOUCHER_TYPES + ADJUSTMENT_VOUCHER_TYPES)}
						AND `qty_delta` > 0 THEN `stock_value_difference` ELSE 0 END
				) AS in_value,
				SUM(
					CASE WHEN `voucher_type` NOT IN {_sql_in(SOLD_VOUCHER_TYPES + ADJUSTMENT_VOUCHER_TYPES)}
						AND `qty_delta` < 0 THEN `qty_delta` ELSE 0 END
				) AS out_qty,
				SUM(
					CASE WHEN `voucher_type` NOT IN {_sql_in(SOLD_VOUCHER_TYPES + ADJUSTMENT_VOUCHER_TYPES)}
						AND `qty_delta` < 0 THEN `stock_value_difference` ELSE 0 END
				) AS out_value
			FROM scoped
			WHERE `posting_date` BETWEEN %(from_date)s AND %(to_date)s
			GROUP BY `item_code`, `warehouse`, `item_name`, `item_group`, `stock_uom`, `branch`
		)
		SELECT
			m.`item_code`, m.`item_name`, m.`item_group`, m.`warehouse`, m.`branch`, m.`stock_uom`,
			COALESCE(o.`opening_qty`, 0) AS opening_qty,
			COALESCE(o.`opening_value`, 0) AS opening_value,
			m.`in_qty`, m.`in_value`,
			m.`out_qty`, m.`out_value`,
			m.`sold_qty`, m.`sold_value`,
			m.`adjustment_qty`, m.`adjustment_value`
		FROM movement m
		LEFT JOIN opening o ON o.`item_code` = m.`item_code` AND o.`warehouse` = m.`warehouse`
		ORDER BY m.`item_code`, m.`warehouse`
		""",
		filters,
		as_dict=True,
	)


def _set_row_values(row, precision):
	row = frappe._dict(row)
	for fieldname in (
		"opening_qty",
		"opening_value",
		"in_qty",
		"in_value",
		"out_qty",
		"out_value",
		"sold_qty",
		"sold_value",
		"adjustment_qty",
		"adjustment_value",
	):
		row[fieldname] = flt(row[fieldname], precision)

	row.closing_qty = flt(
		row.opening_qty + row.in_qty + row.out_qty + row.sold_qty + row.adjustment_qty, precision
	)
	row.closing_value = flt(
		row.opening_value + row.in_value + row.out_value + row.sold_value + row.adjustment_value, precision
	)
	return row


def get_report_summary(data, currency, precision):
	def total(fieldname):
		return flt(sum(row[fieldname] for row in data), precision)

	return [
		{"label": _("Items"), "value": len({row.item_code for row in data}), "indicator": "Blue", "datatype": "Int"},
		{
			"label": _("In Qty"),
			"value": total("in_qty"),
			"indicator": "Green",
			"datatype": "Float",
		},
		{
			"label": _("Out Qty"),
			"value": total("out_qty"),
			"indicator": "Orange",
			"datatype": "Float",
		},
		{
			"label": _("Sold Qty"),
			"value": total("sold_qty"),
			"indicator": "Blue",
			"datatype": "Float",
		},
		{
			"label": _("Adjustment Qty"),
			"value": total("adjustment_qty"),
			"indicator": "Orange" if total("adjustment_qty") else "Green",
			"datatype": "Float",
		},
	]


def get_message():
	return _(
		"Consolidated per Item/Warehouse for the date range. Sold = Sales Invoice, POS Invoice, "
		"Delivery Note (net of returns). Adjustment = Stock Reconciliation only. In/Out = every "
		"other stock movement (Purchase Receipt/Invoice, Stock Entry, purchase returns), split by "
		"the sign of the quantity change. Opening + In + Out + Sold + Adjustment = Closing by "
		"construction - every Stock Ledger Entry falls into exactly one bucket."
	)


def get_columns(currency=None):
	def currency_col(label, fieldname, width=110):
		return {
			"label": label,
			"fieldname": fieldname,
			"fieldtype": "Currency",
			"options": "currency",
			"width": width,
		}

	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 170},
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 110,
		},
		{
			"label": _("Warehouse"),
			"fieldname": "warehouse",
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 160,
		},
		{"label": _("Branch"), "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 130},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Link", "options": "UOM", "width": 70},
		{"label": _("Opening Qty"), "fieldname": "opening_qty", "fieldtype": "Float", "width": 95},
		currency_col(_("Opening Value"), "opening_value"),
		{"label": _("In Qty"), "fieldname": "in_qty", "fieldtype": "Float", "width": 85},
		currency_col(_("In Value"), "in_value"),
		{"label": _("Out Qty"), "fieldname": "out_qty", "fieldtype": "Float", "width": 85},
		currency_col(_("Out Value"), "out_value"),
		{"label": _("Sold Qty"), "fieldname": "sold_qty", "fieldtype": "Float", "width": 85},
		currency_col(_("Sold Value"), "sold_value"),
		{"label": _("Adjustment Qty"), "fieldname": "adjustment_qty", "fieldtype": "Float", "width": 105},
		currency_col(_("Adjustment Value"), "adjustment_value"),
		{"label": _("Closing Qty"), "fieldname": "closing_qty", "fieldtype": "Float", "width": 95},
		currency_col(_("Closing Value"), "closing_value"),
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Data",
			"hidden": 1,
			"default": currency,
		},
	]
