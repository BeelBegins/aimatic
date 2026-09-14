frappe.query_reports["Consolidated Supplier Statement"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "supplier", label: __("Legal Supplier"), fieldtype: "Link", options: "Supplier", reqd: 1 },
		{ fieldname: "report_date", label: __("As On Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
	],
};
