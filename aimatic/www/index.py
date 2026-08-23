import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import escape_html, validate_email_address


sitemap = 1

_ALLOWED_NEEDS = frozenset(
	{
		"ERPNext implementation",
		"Custom Frappe app",
		"Integration or automation",
		"Data migration",
		"Support and optimization",
	}
)
_LEAD_RECIPIENT = "hello@aimatic.tech"


def _clean_text(value, label, maximum, required=True):
	value = str(value or "").strip()
	if required and not value:
		frappe.throw(_("{0} is required.").format(label))
	if len(value) > maximum:
		frappe.throw(_("{0} must be {1} characters or fewer.").format(label, maximum))
	return value


def get_context(context):
	context.no_cache = 1
	context.title = "Aimatic | ERPNext implementation and custom business systems"
	context.meta_description = (
		"Aimatic helps growing businesses implement ERPNext, connect their operations, "
		"and build custom Frappe apps around the way they work."
	)
	context.body_class = "aimatic-public-page"
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.canonical_url = frappe.utils.get_url("/")
	context.logo_url = frappe.utils.get_url("/assets/aimatic/images/aimatic-logo.svg")
	return context


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=5, seconds=60 * 60)
def submit_lead(name=None, email=None, company=None, need=None, message=None, website=None):
	"""Receive a public enquiry without exposing a mailto-only dead end.

	The hidden ``website`` field is a honeypot. Real submissions are recorded as
	a Communication for follow-up and forwarded to the configured website inbox.
	The endpoint deliberately accepts a small, explicit set of enquiry types so
	public input cannot become arbitrary email metadata.
	"""
	if str(website or "").strip():
		return {"ok": True}

	name = _clean_text(name, _("Name"), 120)
	email = validate_email_address(_clean_text(email, _("Email"), 254), throw=True)
	company = _clean_text(company, _("Company"), 160)
	need = _clean_text(need, _("Enquiry type"), 80)
	message = _clean_text(message, _("Message"), 4000, required=False)
	if need not in _ALLOWED_NEEDS:
		frappe.throw(_("Please select a valid enquiry type."))

	message_text = message or _("No additional details provided.")
	content = "<p><strong>Name:</strong> {0}</p>".format(escape_html(name))
	content += "<p><strong>Email:</strong> {0}</p>".format(escape_html(email))
	content += "<p><strong>Company:</strong> {0}</p>".format(escape_html(company))
	content += "<p><strong>Interested in:</strong> {0}</p>".format(escape_html(need))
	content += "<p><strong>Message:</strong></p><p style='white-space:pre-wrap'>{0}</p>".format(
		escape_html(message_text)
	)
	subject = _("Aimatic discovery call — {0}").format(company)

	frappe.get_doc(
		{
			"doctype": "Communication",
			"sender": email,
			"subject": subject,
			"sent_or_received": "Received",
			"content": content,
			"status": "Open",
		}
	).insert(ignore_permissions=True)

	recipient = frappe.conf.get("aimatic_website_recipient") or _LEAD_RECIPIENT
	try:
		frappe.sendmail(
			recipients=[recipient],
			reply_to=email,
			subject=subject,
			content=content,
		)
	except frappe.OutgoingEmailError:
		# Keep the Communication record even when the site has no working email
		# account yet; the enquiry remains visible for an administrator to action.
		frappe.log_error(frappe.get_traceback(), "Aimatic website enquiry email failed")

	return {"ok": True, "message": _("Thanks — we will be in touch shortly.")}
