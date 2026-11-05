from copy import deepcopy
from unittest import TestCase

from aimatic.retail_operations.presentation import digest, field, number, urgent

BASE = "https://example.invalid/app/retail-operations-console"


def snapshot(branch="S1"):
	return dict(
		branch=branch,
		cutoff="2026-11-05 02:30:00",
		reports={
			"Inventory": dict(summary={"Low cover": 2}, rows=[]),
			"Sales": dict(
				date="2026-11-04",
				gross_sales=10000,
				total_refunds=500,
				refund_count=1,
				large_refund_threshold=1000,
				large_refunds=[],
				margins=dict(status="Unavailable"),
				voids=[],
			),
			"Accounts": dict(as_of="2026-11-04", supplier_dues=[], received_unbilled=[]),
			"Purchase": dict(vendors=[]),
		},
	)


def vendor(name, ready=1):
	row = dict(
		item_code="CODE",
		item_name="Product",
		suggested_qty=12,
		stock_uom="Pcs",
		days_cover=1,
		daily_demand=2,
		flags=["Verify stock references"],
	)
	return dict(vendor=name, ready=[deepcopy(row)] * ready, review=[deepcopy(row)], estimated_value=1200)


class PresentationTest(TestCase):
	def test_screened_margins_show_costs_review_and_returns(self):
		from aimatic.retail_operations.margins import summarize
		from aimatic.retail_operations.test_margins import line

		snap = snapshot()
		returned = line("A", sales=-100, cost=0)
		returned.update(is_return=1, stock_qty=-1)
		snap["reports"]["Sales"]["margins"] = summarize([line("A", cost=0), line("B"), returned], {}, {})
		pages = digest([snap], "Sales", BASE)
		self.assertIn("Sales 100 · cost 80", str(pages))
		self.assertIn("Zero recorded cost", str(pages))
		self.assertIn("Returns · separate", str(pages))
		self.assert_limits(pages)

	def test_voids_show_labels_and_unapproved_count(self):
		snap = snapshot()
		snap["reports"]["Sales"]["voids"] = [
			dict(
				pos_profile="S1 Counter 1",
				event_type="Line removed",
				events=4,
				amount=850,
				without_supervisor=3,
			),
			dict(
				pos_profile="S1 Counter 2", event_type="Cart cleared", events=1, amount=120
			),  # older snapshot
		]
		page = str(digest([snap], "Sales", BASE))
		self.assertIn("S1 Counter 1 · Line removed: 4 · value 850 · 3 without supervisor", page)
		self.assertIn("S1 Counter 2 · Cart cleared: 1 · value 120", page)
		self.assertNotIn("Client capture is not connected", page)

	def test_no_voids_is_not_called_disconnected(self):
		page = str(digest([snapshot()], "Sales", BASE))
		self.assertIn("No voids reported for this day", page)

	def assert_limits(self, pages):
		for page in pages:
			self.assertEqual(page["allowed_mentions"], {"parse": []})
			self.assertEqual(page["content"], "")
			self.assertEqual(len(page["embeds"]), 1)
			e = page["embeds"][0]
			self.assertLessEqual(len(e["title"]), 256)
			self.assertLessEqual(len(e["description"]), 4096)
			self.assertLessEqual(len(e["fields"]), 25)
			length = len(e["title"]) + len(e["description"]) + len(e["footer"]["text"])
			for f in e["fields"]:
				self.assertLessEqual(len(f["name"]), 256)
				self.assertLessEqual(len(f["value"]), 1024)
				self.assertTrue(f["value"])
				length += len(f["name"]) + len(f["value"])
			self.assertLessEqual(length, 6000)

	def test_six_branches_stay_in_whole_context_cards(self):
		snaps = [snapshot(f"S{i}") for i in range(6)]
		for channel, count in (("Inventory", 8), ("Accounts", 8), ("Sales", 7)):
			pages = digest(snaps, channel, BASE)
			self.assertEqual(len(pages), count)
			self.assertEqual([p["embeds"][0]["title"] for p in pages[1:7]], [s["branch"] for s in snaps])
			self.assert_limits(pages)

	def test_ready_vendors_before_alphabetical_review_only(self):
		snap = snapshot()
		snap["reports"]["Purchase"]["vendors"] = [vendor("A review only", 0), vendor("Z ready")]
		pages = digest([snap], "Purchase", BASE)
		self.assertEqual(pages[1]["embeds"][0]["title"], "Z ready")
		self.assertIn("HOLD — verify first", str(pages[1]))
		self.assertIn("Order **12 Pcs**", str(pages[1]))
		self.assert_limits(pages)

	def test_limits_and_explicit_omission_with_large_source_lists(self):
		snaps = [snapshot(f"S{i}") for i in range(6)]
		for snap in snaps:
			snap["reports"]["Purchase"]["vendors"] = [
				vendor("Vendor" + str(i) + "x" * 200, 100) for i in range(100)
			]
		pages = digest(snaps, "Purchase", BASE)
		self.assertEqual(len(pages), 8)
		self.assertIn("Showing 7 of 100", str(pages[0]))
		self.assert_limits(pages)

	def test_missing_branch_is_named_not_counted_as_zero(self):
		pages = digest([snapshot(), dict(branch="S2", cutoff="", reports={})], "Inventory", BASE)
		self.assertIn("Reports unavailable", str(pages[0]))
		self.assertIn("S2", str(pages[0]))
		self.assertIn("Oldest evidence: 2026-11-05 02:30 PKT", str(pages[0]))

	def test_currency_totals_do_not_mix(self):
		snap = snapshot()
		snap["reports"]["Accounts"]["supplier_dues"] = [
			dict(supplier="V", currency=c, outstanding=n, overdue=0, bills=1)
			for c, n in (("PKR", 1000), ("USD", 2))
		]
		pages = digest([snap], "Accounts", BASE)
		self.assertIn("PKR **1,000**", str(pages[1]))
		self.assertIn("USD **2**", str(pages[1]))
		self.assertNotIn("1,002", str(pages))

	def test_field_shortening_keeps_whole_item_blocks(self):
		e = dict(title="T", description="", fields=[])
		block = "**Complete item**\nOrder 12 Pcs " + "x" * 360
		field(e, "Items", "\n\n".join([block] * 4))
		value = e["fields"][0]["value"]
		self.assertEqual(value.count("**Complete item**"), 2)
		self.assertIn("full report", value)

	def test_urgent_refund_has_no_fake_stock_or_cover(self):
		body = urgent(snapshot(), dict(subject="Large refund", channel="Sales"), dict(amount=12000), BASE)
		self.assertIn("12,000", str(body))
		self.assertNotIn("Recorded stock", str(body))
		self.assertNotIn("Estimated cover", str(body))
		self.assert_limits([body])

	def test_number_format_preserves_zero_and_unknown(self):
		self.assertEqual(number(12000), "12,000")
		self.assertEqual(number(0), "0")
		self.assertEqual(number(None), "—")
