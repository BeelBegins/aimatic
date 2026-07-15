import frappe


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/uploadimageproduct"
		raise frappe.Redirect
	frappe.only_for("System Manager")
	context.no_cache = 1
	context.title = "Product Image Studio"
	context.csrf_token = frappe.sessions.get_csrf_token()
	return context
