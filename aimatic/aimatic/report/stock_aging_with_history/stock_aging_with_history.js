frappe.query_reports["Stock Aging with History"] = {
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
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "Link",
			options: "Branch",
			reqd: 1,
		},
		{
			fieldname: "as_on_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: function () {
				return {
					filters: {
						company: frappe.query_report.get_filter_value("company"),
					},
				};
			},
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{
			fieldname: "include_unmatched",
			label: __("Include Unmatched History"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "include_live",
			label: __("Include Live ERPNext Purchases"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "range1",
			label: __("Age Range 1 (days)"),
			fieldtype: "Int",
			default: 30,
		},
		{
			fieldname: "range2",
			label: __("Age Range 2 (days)"),
			fieldtype: "Int",
			default: 60,
		},
		{
			fieldname: "range3",
			label: __("Age Range 3 (days)"),
			fieldtype: "Int",
			default: 90,
		},
	],
};
