// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

frappe.query_reports["Stock Reconciliation Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "name",
			label: __("Reconciliation No"),
			fieldtype: "Link",
			options: "Stock Reconciliation",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				filters: {
					company: frappe.query_report.get_filter_value("company"),
				},
			}),
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "include_cancelled",
			label: __("Include Cancelled"),
			fieldtype: "Check",
			default: 0,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "qty_diff" && data && data.qty_diff) {
			const color = data.qty_diff > 0 ? "green" : "red";
			return `<span style="color:${color}">${value}</span>`;
		}
		if (column.fieldname === "status" && data) {
			const color = data.docstatus === 1 ? "green" : "grey";
			return `<span class="indicator-pill ${color}">${value}</span>`;
		}
		return value;
	},
};
