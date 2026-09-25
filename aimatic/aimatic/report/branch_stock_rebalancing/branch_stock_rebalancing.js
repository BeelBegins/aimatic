// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

frappe.query_reports["Branch Stock Rebalancing"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{ fieldname: "receiver_branch", label: __("Move To Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "donor_branch", label: __("Move From Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "history_days", label: __("Sales Days"), fieldtype: "Int", default: 28 },
		{ fieldname: "target_cover_days", label: __("Target Cover Days"), fieldtype: "Int", default: 7 },
		{ fieldname: "donor_keep_days", label: __("Donor Keeps Days"), fieldtype: "Int", default: 30 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "priority" && data) {
			const color = { "Stock-out": "red", Urgent: "orange", "Top up": "blue" }[data.priority];
			return `<span style="color:${color};font-weight:600">${value}</span>`;
		}
		return value;
	},
};
