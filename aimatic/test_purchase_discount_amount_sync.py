"""Typed Discount Amount -> Discount % in the PO/PR server calculation scripts.

Runs the real fixture block (extracted by its marker comment) with a stubbed
``frappe.db.get_value``; no site or database is needed.
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

MARKER = "# TYPED DISCOUNT AMOUNT -> DISCOUNT %"
END = "row.custom_discount_per = derived_per"
FIXTURE = Path(__file__).parent / "fixtures" / "server_script.json"


def _block(script_name: str) -> str:
	for doc in json.loads(FIXTURE.read_text()):
		if doc["name"] == script_name:
			script = doc["script"].replace("\r\n", "\n")
			start = script.index(MARKER)
			start = script.rindex("\n", 0, script.rindex("# ---", 0, start)) + 1
			end = script.index(END) + len(END)
			return "\n".join(line[4:] for line in script[start:end].split("\n"))
	raise AssertionError(script_name)


def _run(script_name, row, saved, **env):
	frappe = SimpleNamespace(db=SimpleNamespace(get_value=lambda *a, **k: saved))
	scope = {"frappe": frappe, "row": row, "effective_return": False, **env}
	exec(_block(script_name), scope)
	return scope["discount_per"], row


def _row(amount, per=0.0):
	return SimpleNamespace(
		name="ROW1", custom_discount_amnt=amount, custom_discount_per=per
	)


class TestTypedDiscountAmount(unittest.TestCase):
	SCRIPTS = ("PurchaseOrderCalculation", "PRV1")

	def test_new_row_amount_only_derives_percent(self):
		for name in self.SCRIPTS:
			per, row = _run(
				name, _row(50), None, vendor_rate=100.0, qty=10.0, scheme_qty=0.0, discount_per=0.0
			)
			self.assertAlmostEqual(per, 5.0, places=6, msg=name)
			self.assertAlmostEqual(row.custom_discount_per, 5.0, places=6, msg=name)

	def test_paid_qty_excludes_scheme(self):
		per, _ = _run(
			"PurchaseOrderCalculation",
			_row(45),
			None,
			vendor_rate=100.0,
			qty=10.0,
			scheme_qty=1.0,
			discount_per=0.0,
		)
		self.assertAlmostEqual(per, 5.0, places=6)

	def test_saved_row_amount_edited_percent_unchanged(self):
		saved = SimpleNamespace(custom_discount_per=5.0, custom_discount_amnt=50.0)
		per, _ = _run(
			"PRV1", _row(80, 5.0), saved, vendor_rate=100.0, qty=10.0, scheme_qty=0.0, discount_per=5.0
		)
		self.assertAlmostEqual(per, 8.0, places=6)

	def test_percent_edit_wins_when_both_changed(self):
		saved = SimpleNamespace(custom_discount_per=5.0, custom_discount_amnt=50.0)
		per, _ = _run(
			"PRV1", _row(80, 10.0), saved, vendor_rate=100.0, qty=10.0, scheme_qty=0.0, discount_per=10.0
		)
		self.assertEqual(per, 10.0)

	def test_rounding_echo_is_left_alone(self):
		# Amount stored from a % calculation must not nudge the % on re-save.
		saved = SimpleNamespace(custom_discount_per=3.333333, custom_discount_amnt=33.33)
		per, _ = _run(
			"PurchaseOrderCalculation",
			_row(33.34, 3.333333),
			saved,
			vendor_rate=100.0,
			qty=10.0,
			scheme_qty=0.0,
			discount_per=3.333333,
		)
		self.assertEqual(per, 3.333333)

	def test_zero_rate_or_paid_qty_is_ignored(self):
		for kw in (
			dict(vendor_rate=0.0, qty=10.0, scheme_qty=0.0),
			dict(vendor_rate=100.0, qty=5.0, scheme_qty=5.0),
		):
			per, _ = _run("PurchaseOrderCalculation", _row(50), None, discount_per=0.0, **kw)
			self.assertEqual(per, 0.0)

	def test_pr_return_is_ignored(self):
		per, _ = _run(
			"PRV1",
			_row(50),
			None,
			vendor_rate=100.0,
			qty=10.0,
			scheme_qty=0.0,
			discount_per=0.0,
			effective_return=True,
		)
		self.assertEqual(per, 0.0)


if __name__ == "__main__":
	unittest.main()
