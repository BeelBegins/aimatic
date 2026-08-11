"""Keep Purchase Invoice with Item Image HTML in sync with aimatic layout (barcode column)."""

from __future__ import annotations

from pathlib import Path

import frappe

LAYOUT = (
	Path(__file__).resolve().parents[1]
	/ "print_layouts"
	/ "purchase_invoice_with_item_image.html"
)

FMT = "Purchase Invoice with Item Image"
PS = "Purchase Invoice-main-default_print_format"


def execute():
	html = LAYOUT.read_text(encoding="utf-8")
	if frappe.db.exists("Print Format", FMT):
		frappe.db.set_value(
			"Print Format",
			FMT,
			{"html": html, "custom_format": 1, "print_format_type": "Jinja"},
			update_modified=False,
		)
		frappe.db.set_value(
			"Print Format",
			FMT,
			"modified",
			"2026-08-08 21:35:00",
			update_modified=False,
		)

	if frappe.db.exists("Property Setter", PS):
		frappe.db.set_value("Property Setter", PS, "value", FMT)

	frappe.clear_cache()
