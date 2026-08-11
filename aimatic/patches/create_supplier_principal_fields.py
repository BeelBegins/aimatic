from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	"""Principal tagging for multi-company distributors (no Accounting Dimension).

	- Supplier.custom_principals: allowed Principals for that legal party
	- PO/PR/PI.custom_principal: KPO-selected principal for the purchase
	"""
	create_custom_fields(
		{
			"Supplier": [
				{
					"fieldname": "custom_principals_section",
					"label": "Principals",
					"fieldtype": "Section Break",
					"insert_after": "custom_legacy_supplier_code",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_principals",
					"label": "Principals",
					"fieldtype": "Table",
					"options": "Supplier Principal",
					"insert_after": "custom_principals_section",
					"description": (
						"Brand houses / company lines this distributor supplies. "
						"When this list is non-empty, Purchase Order / Receipt / "
						"Invoice must tag one of these principals."
					),
				},
			],
			"Purchase Order": [
				{
					"fieldname": "custom_principal",
					"label": "Principal",
					"fieldtype": "Link",
					"options": "Principal",
					"insert_after": "supplier",
					"in_standard_filter": 1,
					"in_list_view": 1,
					"description": (
						"Required when the supplier has Principals configured. "
						"Blank dropdown of that supplier's allowed list only."
					),
				},
			],
			"Purchase Receipt": [
				{
					"fieldname": "custom_principal",
					"label": "Principal",
					"fieldtype": "Link",
					"options": "Principal",
					"insert_after": "supplier",
					"in_standard_filter": 1,
					"in_list_view": 1,
					"description": (
						"Required when the supplier has Principals configured. "
						"Copied from Purchase Order when blank."
					),
				},
			],
			"Purchase Invoice": [
				{
					"fieldname": "custom_principal",
					"label": "Principal",
					"fieldtype": "Link",
					"options": "Principal",
					"insert_after": "supplier",
					"in_standard_filter": 1,
					"in_list_view": 1,
					"description": (
						"Required when the supplier has Principals configured. "
						"Copied from Purchase Receipt / Order when blank."
					),
				},
			],
		},
		update=True,
	)
