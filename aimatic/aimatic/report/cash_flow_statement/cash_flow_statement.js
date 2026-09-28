// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

frappe.query_reports["Cash Flow Statement"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
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
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
		},
		{
			fieldname: "account",
			label: __("Cash/Bank Account"),
			fieldtype: "Link",
			options: "Account",
			get_query: () => ({
				filters: {
					company: frappe.query_report.get_filter_value("company"),
					account_type: ["in", ["Cash", "Bank"]],
					is_group: 0,
				},
			}),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && ["total_receipts", "total_payments", "closing_balance"].includes(data.row_type)) {
			return `<strong>${value}</strong>`;
		}
		return value;
	},
};
