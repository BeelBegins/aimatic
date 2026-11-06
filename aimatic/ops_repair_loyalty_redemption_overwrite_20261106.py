"""One-off repair: POS redemption sales whose loyalty ledger was corrupted by
_correct_loyalty_point_entry matching core's redemption row instead of the
earning row (szl, 2026-10-30..2026-11-06).

For each POS Invoice that still has core redemption rows (invoice_type POS
Invoice, invoice=<it>, redeem_against set):
- earning row -> item-group-weighted points (same calculation as the hook,
  net of anything already returned); expiry kept;
- core redemption rows -> deleted. The aimatic "Redemption debit for ..."
  Journal Entry row stays the single redemption debit, as for every other
  redemption sale.

Touches Loyalty Point Entry only (no GL, stock, payments). Idempotent: once
repaired an invoice has no redemption rows and is not selected again.

Dry run (default, read-only):
    bench --site szl execute aimatic.ops_repair_loyalty_redemption_overwrite_20261106.run
Apply (after backup and approval):
    bench --site szl execute aimatic.ops_repair_loyalty_redemption_overwrite_20261106.run --kwargs '{"dry_run": false}'
"""

from collections import defaultdict

import frappe
from frappe.utils import cint, flt

from aimatic.loyalty.events import _compute_item_group_points, _earning_entry_name
from aimatic.pos_shared import returned_qty_by_row


def _balance(customer):
	return flt(
		frappe.db.sql("select sum(loyalty_points) from `tabLoyalty Point Entry` where customer=%s", customer)[
			0
		][0]
	)


def run(dry_run=True):
	dry_run = dry_run not in (False, 0, "0", "false", "False")

	invoices = frappe.db.sql_list(
		"""select distinct invoice from `tabLoyalty Point Entry`
		where invoice_type='POS Invoice' and ifnull(redeem_against,'')!=''
		order by creation"""
	)

	delta_by_customer = defaultdict(float)
	invoice_rows = defaultdict(list)
	for name in invoices:
		inv = frappe.get_doc("POS Invoice", name)
		earn_name = _earning_entry_name(name)
		earn = (
			frappe.db.get_value(
				"Loyalty Point Entry", earn_name, ["loyalty_points", "purchase_amount"], as_dict=True
			)
			if earn_name
			else None
		)
		points, eligible = _compute_item_group_points(inv, returned_qty_by_row(name))
		red_rows = frappe.get_all(
			"Loyalty Point Entry",
			filters={"invoice_type": "POS Invoice", "invoice": name, "redeem_against": ["is", "set"]},
			fields=["name", "loyalty_points"],
		)
		red_sum = sum(flt(r.loyalty_points) for r in red_rows)
		debited = frappe.db.get_value(
			"Loyalty Point Entry",
			{"discretionary_reason": f"Redemption debit for POS Invoice {name}"},
			"loyalty_points",
		)
		earn_pts = flt(earn.loyalty_points) if earn else 0
		delta = (points - earn_pts if earn else 0) - red_sum
		delta_by_customer[inv.customer] += delta
		invoice_rows[inv.customer].append(
			frappe._dict(
				invoice=name,
				posted=str(inv.posting_date),
				counter=inv.pos_profile,
				cashier=inv.owner,
				total=flt(inv.grand_total),
				redeemed=cint(inv.loyalty_points),
				debit_row=debited,
				earn_now=earn_pts,
				earn_fixed=points,
				red_rows=[(r.name, flt(r.loyalty_points)) for r in red_rows],
				delta=delta,
			)
		)

		if not dry_run:
			if earn_name:
				frappe.db.set_value(
					"Loyalty Point Entry",
					earn_name,
					{"loyalty_points": points, "purchase_amount": eligible},
				)
			for r in red_rows:
				frappe.delete_doc("Loyalty Point Entry", r.name, ignore_permissions=True, force=True)

	mode = "DRY RUN -- nothing written" if dry_run else "APPLIED"
	print(f"{mode}: {len(invoices)} invoice(s), {len(invoice_rows)} customer(s)\n")
	total = 0.0
	for customer, rows in invoice_rows.items():
		before = _balance(customer) if dry_run else _balance(customer) - delta_by_customer[customer]
		after = before + delta_by_customer[customer]
		mobile = frappe.db.get_value("Customer", customer, "mobile_no") or ""
		print(f"{customer} {mobile}: balance {before:g} -> {after:g} ({delta_by_customer[customer]:+g})")
		for r in rows:
			print(
				f"   {r.invoice} {r.posted} {r.counter} {r.cashier} total {r.total:g} redeemed {r.redeemed}"
				f" debit_row {r.debit_row} | earning {r.earn_now:g}->{r.earn_fixed} | core redemption rows"
				f" {r.red_rows} -> deleted"
			)
		total += delta_by_customer[customer]
	print(f"\nNet points change: {total:+g}")

	if not dry_run:
		frappe.db.commit()
