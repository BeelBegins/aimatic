frappe.query_reports["Principal Item Mapping Audit"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company", default: frappe.defaults.get_user_default("Company"), reqd: 1 },
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier", reqd: 1 },
		{ fieldname: "from_date", label: __("Unclassified From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Unclassified To"), fieldtype: "Date" },
		{ fieldname: "status", label: __("Mapping Status"), fieldtype: "Select", options: "\nApproved Item Mapping\nUnique Later Evidence\nConflicting Later Evidence\nNo Later Evidence" },
	],
};
