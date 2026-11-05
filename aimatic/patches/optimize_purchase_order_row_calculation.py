"""Install the reviewed per-row PO calculation without replacing an unknown live edit."""

import hashlib
import json
from pathlib import Path

import frappe

SCRIPT = "Client Script for Tax Cal For Purchase Order"
# Script as live on szl before this change (fixture modified 2026-07-09).
BEFORE_SHA256 = "c56b99039d0bb81e94d5f97185143ee69efc05732890b85486f6aa5547726f0c"


def execute():
	if not frappe.db.exists("Client Script", SCRIPT):
		return

	path = Path(__file__).resolve().parents[1] / "fixtures" / "client_script.json"
	optimized = next(row["script"] for row in json.loads(path.read_text()) if row["name"] == SCRIPT)
	doc = frappe.get_doc("Client Script", SCRIPT)
	if doc.script == optimized:
		return
	if hashlib.sha256((doc.script or "").encode()).hexdigest() != BEFORE_SHA256:
		frappe.throw(f"{SCRIPT} has changed; review it before applying the row calculation update.")

	doc.script = optimized
	doc.save(ignore_permissions=True)
	frappe.clear_cache(doctype="Purchase Order")
