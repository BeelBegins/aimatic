import frappe


def execute():
	if frappe.db.exists("OAuth Client", {"app_name": "Aimatic Shopping"}):
		return
	doc = frappe.get_doc(
		{
			"doctype": "OAuth Client",
			"app_name": "Aimatic Shopping",
			"skip_authorization": 1,
			"scopes": "shopping-customer",
			"redirect_uris": "com.beelbegins.aimaticshopping://oauth/callback",
			"default_redirect_uri": "com.beelbegins.aimaticshopping://oauth/callback",
			"grant_type": "Authorization Code",
			"response_type": "Code",
			"token_endpoint_auth_method": "None",
		}
	)
	for role in ("Customer", "System Manager"):
		doc.append("allowed_roles", {"role": role})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
