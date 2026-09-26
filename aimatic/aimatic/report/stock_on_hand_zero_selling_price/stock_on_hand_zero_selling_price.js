frappe.query_reports["Stock On Hand With Zero Selling Price"] = {
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
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "selling_price" && data) {
			return `<span style="color:#b42318;font-weight:600">${value}</span>`;
		}
		if (column.fieldname === "price_status" && data) {
			return `<span class="indicator-pill red">${value}</span>`;
		}
		return value;
	},
};
