import frappe

_APP_NAME = "Aimatic Stock Receiving Android"
_REDIRECT_URI = "com.beelbegins.aimaticstockreceiving://oauth/callback"


def execute():
	if frappe.db.exists("OAuth Client", {"app_name": _APP_NAME}):
		return
	doc = frappe.get_doc({
		"doctype": "OAuth Client",
		"app_name": _APP_NAME,
		"skip_authorization": 1,
		"scopes": "stock-receiving",
		"redirect_uris": _REDIRECT_URI,
		"default_redirect_uri": _REDIRECT_URI,
		"grant_type": "Authorization Code",
		"response_type": "Code",
		"token_endpoint_auth_method": "None",
	})
	for role in ("Stock User", "Stock Manager", "Purchase User", "Purchase Manager", "System Manager"):
		doc.append("allowed_roles", {"role": role})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
