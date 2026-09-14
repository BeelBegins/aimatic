frappe.query_reports["Principal-wise Payable Outstanding"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "report_date", label: __("As On Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "principal", label: __("Principal"), fieldtype: "Link", options: "Principal" },
		{ fieldname: "range", label: __("Ageing Range"), fieldtype: "Data", default: "30, 60, 90, 120" },
		{ fieldname: "show_future_payments", label: __("Show Future Payments"), fieldtype: "Check", default: 0 },
	],
};
