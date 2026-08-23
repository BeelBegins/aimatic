import frappe

# Public OAuth2 client (no embedded secret — token_endpoint_auth_method "None")
# for the Android POS app's per-cashier Authorization Code + PKCE login. The
# client_id this creates is not sensitive (it's a public OAuth2 identifier,
# safe to ship in the APK); no client_secret is ever given to Android.
_APP_NAME = "Aimatic POS Android"
_REDIRECT_URI = "com.beelbegins.aimaticpos://oauth/callback"
_SCOPES = "pos-device"
_ALLOWED_ROLES = ["POS User", "POS Supervisor", "System Manager"]


def execute():
	if frappe.db.exists("OAuth Client", {"app_name": _APP_NAME}):
		return

	doc = frappe.get_doc(
		{
			"doctype": "OAuth Client",
			"app_name": _APP_NAME,
			"skip_authorization": 1,
			"scopes": _SCOPES,
			"redirect_uris": _REDIRECT_URI,
			"default_redirect_uri": _REDIRECT_URI,
			"grant_type": "Authorization Code",
			"response_type": "Code",
			"token_endpoint_auth_method": "None",
		}
	)
	for role in _ALLOWED_ROLES:
		doc.append("allowed_roles", {"role": role})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
