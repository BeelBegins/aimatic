"""Install the reviewed PR quantity race fix without replacing an unknown live edit."""

import hashlib
import json
from pathlib import Path

import frappe

BEFORE_SHA256 = "b42cdbc6e14ba71c282a953f67f000c3d33f600cd85781a848d6ad540d4f18fa"


def execute():
	if not frappe.db.exists("Client Script", "prv1"):
		return

	path = Path(__file__).resolve().parents[1] / "fixtures" / "client_script.json"
	fixed = next(row["script"] for row in json.loads(path.read_text()) if row["name"] == "prv1")
	doc = frappe.get_doc("Client Script", "prv1")
	if doc.script == fixed:
		return
	if hashlib.sha256((doc.script or "").encode()).hexdigest() != BEFORE_SHA256:
		frappe.throw("Purchase Receipt client script prv1 has changed; review it before applying the quantity fix.")

	doc.script = fixed
	doc.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Purchase Receipt")
