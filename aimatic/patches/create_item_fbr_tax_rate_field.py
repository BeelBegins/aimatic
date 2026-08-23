import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

DEFAULT_FBR_TAX_CATEGORY = "Exempt goods"


def execute():
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_fbr_tax_rate",
					"label": "FBR Tax Rate",
					"fieldtype": "Percent",
					"read_only": 1,
					"fetch_from": "custom_fbr_tax_category.tax_rate",
					"insert_after": "custom_fbr_tax_category",
				}
			]
		},
		update=False,
	)

	if frappe.db.exists("FBR Tax Category", DEFAULT_FBR_TAX_CATEGORY):
		frappe.db.sql(
			"""
            update `tabItem`
            set custom_fbr_tax_category = %s
            where ifnull(custom_fbr_tax_category, '') = ''
            """,
			(DEFAULT_FBR_TAX_CATEGORY,),
		)

	frappe.db.sql(
		"""
        update `tabItem` i
        inner join `tabFBR Tax Category` c on c.name = i.custom_fbr_tax_category
        set i.custom_fbr_tax_rate = c.tax_rate
        where ifnull(i.custom_fbr_tax_category, '') != ''
        """
	)

	frappe.db.commit()
