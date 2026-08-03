// Copyright (c) 2026, Ai Matic and contributors
// For license information, please see license.txt

frappe.query_reports["Branch Price Sheet"] = {
	filters: [
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
			default: frappe.defaults.get_user_default("Branch"),
			reqd: 1,
		},
	],
};
