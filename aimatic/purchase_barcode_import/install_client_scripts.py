"""Install/update Client Scripts so the Resolve button works without gunicorn reload."""

from pathlib import Path

import frappe

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "public" / "js" / "purchase_barcode_import.js"

_SCRIPTS = (
	("Purchase Order", "Resolve Items from Barcodes (PO)"),
	("Purchase Receipt", "Resolve Items from Barcodes (PR)"),
)


def install_client_scripts():
	script = _SCRIPT_PATH.read_text(encoding="utf-8")
	for dt, name in _SCRIPTS:
		if frappe.db.exists("Client Script", name):
			doc = frappe.get_doc("Client Script", name)
			doc.enabled = 1
			doc.script = script
			doc.view = "Form"
			doc.dt = dt
			doc.module = "Aimatic"
			doc.save(ignore_permissions=True)
			action = "updated"
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Client Script",
					"name": name,
					"dt": dt,
					"view": "Form",
					"enabled": 1,
					"module": "Aimatic",
					"script": script,
				}
			)
			doc.insert(ignore_permissions=True)
			action = "inserted"
		print(f"{action} {name}")
	frappe.clear_cache()
	return [name for _, name in _SCRIPTS]
