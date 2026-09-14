from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Add the item mapping and a supplier-wise rollout gate.

	The gate intentionally defaults off: existing multi-principal suppliers must
	be mapped and reviewed before strict line validation is enabled.
	"""
	create_custom_fields(
		{
			"Item": [
				{
					"fieldname": "custom_principal",
					"label": "Principal",
					"fieldtype": "Link",
					"options": "Principal",
					"insert_after": "brand",
					"in_standard_filter": 1,
					"in_list_view": 1,
					"description": "Approved brand house / business principal for reporting and purchase validation.",
				},
			],
			"Supplier": [
				{
					"fieldname": "custom_enforce_item_principal",
					"label": "Enforce Item Principal",
					"fieldtype": "Check",
					"default": "0",
					"insert_after": "custom_principals",
					"description": "Require every purchase row to match the document Principal. Enable only after mapping review.",
				},
			],
		},
		update=True,
	)
