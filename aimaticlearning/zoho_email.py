from __future__ import annotations

from pathlib import Path

import frappe
from frappe import _
from frappe.utils import validate_email_address

DEFAULT_ZOHO_EMAIL = "hello@aimatic.tech"
DEFAULT_ZOHO_SMTP = "smtp.zoho.com"
DEFAULT_ZOHO_SMTP_PORT = 587
DEFAULT_ZOHO_ACCOUNT_NAME = "Aimatic Zoho"
ZOHO_APP_PASSWORD_SECRET = Path.home() / ".local/share/aimatic/site-secrets/lms.aimatic.tech.zoho-app-password"


def _read_zoho_app_password(password: str | None = None) -> str:
	if password:
		return password.strip()
	if not ZOHO_APP_PASSWORD_SECRET.is_file():
		frappe.throw(
			_(
				"Zoho app password not provided. Save it to {0} or pass password= to configure_zoho_outgoing_email."
			).format(ZOHO_APP_PASSWORD_SECRET)
		)
	return ZOHO_APP_PASSWORD_SECRET.read_text(encoding="utf-8").strip()


def configure_zoho_outgoing_email(
	email_id: str = DEFAULT_ZOHO_EMAIL,
	password: str | None = None,
	smtp_server: str = DEFAULT_ZOHO_SMTP,
	smtp_port: int = DEFAULT_ZOHO_SMTP_PORT,
	send_test_to: str | None = None,
	account_name: str = DEFAULT_ZOHO_ACCOUNT_NAME,
	test_subject: str | None = None,
) -> dict:
	"""Create or update default outgoing Zoho SMTP on any Frappe site."""
	validate_email_address(email_id, True)
	app_password = _read_zoho_app_password(password)

	existing = frappe.db.get_value("Email Account", {"email_id": email_id}, "name")
	if existing:
		doc = frappe.get_doc("Email Account", existing)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": account_name,
				"email_id": email_id,
			}
		)

	doc.email_account_name = account_name
	doc.email_id = email_id
	doc.login_id = email_id
	doc.password = app_password
	doc.enable_outgoing = 1
	doc.enable_incoming = 0
	doc.default_outgoing = 1
	doc.use_tls = 1
	doc.use_ssl = 0
	doc.smtp_server = smtp_server
	doc.smtp_port = smtp_port
	doc.always_use_account_email_id_as_sender = 1
	doc.no_smtp_authentication = 0
	doc.save(ignore_permissions=True)

	frappe.db.sql(
		"""
		UPDATE `tabEmail Account`
		SET default_outgoing = 0
		WHERE name != %s AND default_outgoing = 1
		""",
		doc.name,
	)

	test_result = None
	if send_test_to:
		validate_email_address(send_test_to, True)
		site = frappe.local.site
		subject = test_subject or f"Aimatic email test ({site})"
		frappe.sendmail(
			recipients=[send_test_to],
			subject=subject,
			message=f"<p>Zoho outgoing email is configured on {site}.</p>",
			delayed=False,
		)
		test_result = send_test_to

	frappe.db.commit()
	return {
		"email_account": doc.name,
		"email_id": email_id,
		"smtp_server": smtp_server,
		"smtp_port": smtp_port,
		"default_outgoing": 1,
		"test_sent_to": test_result,
		"site": frappe.local.site,
	}
