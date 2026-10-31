import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

# Same limits as Posapplication src/domain/weighted-barcode.ts normalizeScaleRules, so a
# saved rule is never rejected by the till.
_KIND = {"Weight": "weight", "Price": "price", "Piece Count": "count"}
_UNIT = {"Gram": "g", "Kg": "kg"}


class WeighingScaleSettings(Document):
	def validate(self):
		if cint(self.enabled) and not self.rules:
			frappe.throw(_("Add at least one label rule before enabling Weighing Scale Settings"))
		for row in self.rules:
			label = _("Row {0}").format(row.idx)
			row.label_prefix = (row.label_prefix or "").strip()
			row.erp_barcode_prefix = (row.erp_barcode_prefix or "").strip()
			if not re.fullmatch(r"\d{0,6}", row.label_prefix):
				frappe.throw(_("{0}: Label Prefix must be 0-6 digits").format(label))
			if not 1 <= cint(row.item_digits) <= 10 or not 1 <= cint(row.value_digits) <= 10:
				frappe.throw(_("{0}: Item Digits and Value Digits must be 1-10").format(label))
			if not 0 <= cint(row.decimals) <= cint(row.value_digits):
				frappe.throw(_("{0}: Decimals must be 0-{1}").format(label, cint(row.value_digits)))
			if not re.fullmatch(r"[A-Za-z0-9-]{0,8}", row.erp_barcode_prefix):
				frappe.throw(_("{0}: ERP Barcode Starts With may only have letters, numbers or '-' (max 8)").format(label))
			if _length(row) > 20:
				frappe.throw(_("{0}: total label length is over 20 digits").format(label))
		rows = list(self.rules)
		for a, first in enumerate(rows):
			for second in rows[a + 1 :]:
				if _length(first) == _length(second) and (
					first.label_prefix.startswith(second.label_prefix) or second.label_prefix.startswith(first.label_prefix)
				):
					frappe.throw(
						_("Rows {0} and {1} would both match the same labels (same length and overlapping prefix)").format(
							first.idx, second.idx
						)
					)


def _length(row) -> int:
	return len(row.label_prefix or "") + cint(row.item_digits) + cint(row.value_digits) + cint(row.check_digit)


def pos_scale_rules() -> dict:
	"""Rules in the POS client's ScaleRule shape. managed=False leaves each till on its own rules."""
	settings = frappe.get_cached_doc("Weighing Scale Settings")
	if not cint(settings.enabled) or not settings.rules:
		return {"managed": False, "rules": [], "modified": str(settings.modified or "")}
	return {
		"managed": True,
		"modified": str(settings.modified or ""),
		"rules": [
			{
				"name": row.rule_name or _("Rule {0}").format(row.idx),
				"prefix": row.label_prefix or "",
				"itemLength": cint(row.item_digits),
				"valueLength": cint(row.value_digits),
				"kind": _KIND.get(row.value_type, "weight"),
				"unit": _UNIT.get(row.weight_unit, "g"),
				"decimals": cint(row.decimals),
				"checkDigit": bool(cint(row.check_digit)),
				"lookupPrefix": row.erp_barcode_prefix or "",
			}
			for row in settings.rules
		],
	}
