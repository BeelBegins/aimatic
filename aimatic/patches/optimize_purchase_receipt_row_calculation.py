"""Install the reviewed per-row PR calculation without replacing an unknown live edit."""

import hashlib
import json
from pathlib import Path

import frappe

# prv1 as installed by fix_purchase_receipt_quantity_race (aimatic c1e037d).
BEFORE_SHA256 = "22b268c7c4530d25567dbdb54512acd9fdd99c7108052769ea3b0e1116bb163f"


def execute():
	if not frappe.db.exists("Client Script", "prv1"):
		return

	path = Path(__file__).resolve().parents[1] / "fixtures" / "client_script.json"
	optimized = next(row["script"] for row in json.loads(path.read_text()) if row["name"] == "prv1")
	doc = frappe.get_doc("Client Script", "prv1")
	if doc.script == optimized:
		return
	if hashlib.sha256((doc.script or "").encode()).hexdigest() != BEFORE_SHA256:
		frappe.throw(
			"Purchase Receipt client script prv1 has changed; review it before applying the row calculation update."
		)

	doc.script = optimized
	doc.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Purchase Receipt")
