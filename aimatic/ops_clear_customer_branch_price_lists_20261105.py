"""One-off: clear branch counter price lists from every Customer.

Root cause: ERPNext POS Invoice.set_pos_fields prices a sale from
Customer.default_price_list before the selling counter's POS Profile list (on
build and again on validate). Customers carried branch lists from the S1 iPOS
import (stamped with the Selling Settings default S1 list by the old
offline_pos.customer_validation hook), the S2 import (explicit S2 list), till
customer creation (creating counter's list) and branch customer setup, so a
branch-A customer was charged branch-A prices at branch B. Forward fix: the
hook no longer stamps a list and validate_no_branch_price_list rejects one.

Scope: every Customer whose default_price_list is a branch list (see
customer_validation.get_branch_price_lists), INCLUDING the branch Walk in /
Food Panda / Home Delivery customers (user decision 2026-11-05). Non-branch
lists (none on szl at time of writing) are left alone. Customer.modified is not
touched (tills do not cache default_price_list). Idempotent: a re-run finds 0.

Run:
  bench --site SITE execute aimatic.ops_clear_customer_branch_price_lists_20261105.run
      (dry run: counts only)
  bench --site SITE execute aimatic.ops_clear_customer_branch_price_lists_20261105.run --kwargs "{'dry_run': 0}"
      (apply: writes private/files/customer_price_list_rollback_<ts>.csv first)

Rollback:
  bench --site SITE execute aimatic.ops_clear_customer_branch_price_lists_20261105.rollback --kwargs "{'csv_name': 'customer_price_list_rollback_<ts>.csv'}"
  Restores the old list only where the Customer's list is still blank.
"""

from __future__ import annotations

import csv
import os
from collections import Counter

import frappe
from frappe.utils import now_datetime

from aimatic.offline_pos.customer_validation import get_branch_price_lists

CSV_PREFIX = "customer_price_list_rollback_"


def _affected(lists):
	if not lists:
		return []
	return frappe.db.sql(
		"""
		select name, default_price_list
		from `tabCustomer`
		where default_price_list in %(lists)s
		order by name
		""",
		{"lists": tuple(lists)},
		as_dict=True,
	)


def _private_path(name):
	return frappe.get_site_path("private", "files", name)


def run(dry_run=1):
	dry_run = int(dry_run)
	lists = sorted(get_branch_price_lists())
	rows = _affected(lists)
	pos_customers = set(frappe.get_all("POS Profile", pluck="customer"))
	summary = {
		"dry_run": dry_run,
		"branch_price_lists": lists,
		"customers_to_clear": len(rows),
		"by_price_list": dict(Counter(r.default_price_list for r in rows)),
		"pos_profile_customers_included": sorted(r.name for r in rows if r.name in pos_customers),
		"non_branch_lists_left": dict(
			frappe.db.sql(
				"""
				select default_price_list, count(*) from `tabCustomer`
				where coalesce(default_price_list, '') <> ''
				  and default_price_list not in %(lists)s
				group by default_price_list
				""",
				{"lists": tuple(lists) or ("",)},
			)
		),
	}
	if dry_run or not rows:
		return summary

	csv_name = f"{CSV_PREFIX}{now_datetime().strftime('%Y%m%d_%H%M%S')}.csv"
	path = _private_path(csv_name)
	with open(path, "w", newline="") as handle:
		writer = csv.writer(handle)
		writer.writerow(["name", "default_price_list"])
		for row in rows:
			writer.writerow([row.name, row.default_price_list])
		handle.flush()
		os.fsync(handle.fileno())

	frappe.db.sql(
		"""
		update `tabCustomer`
		set default_price_list = NULL
		where default_price_list in %(lists)s
		""",
		{"lists": tuple(lists)},
	)
	remaining = len(_affected(lists))
	if remaining:
		frappe.db.rollback()
		frappe.throw(f"{remaining} Customers still hold a branch price list; rolled back.")
	frappe.db.commit()
	frappe.clear_cache(doctype="Customer")

	summary.update({"rollback_csv": csv_name, "cleared": len(rows), "remaining": remaining})
	return summary


def rollback(csv_name, dry_run=1):
	dry_run = int(dry_run)
	if not csv_name.startswith(CSV_PREFIX) or "/" in csv_name:
		frappe.throw("csv_name must be a rollback CSV file name from run().")
	with open(_private_path(csv_name), newline="") as handle:
		rows = list(csv.DictReader(handle))

	restorable = [
		row
		for row in rows
		if frappe.db.exists("Customer", row["name"])
		and not frappe.db.get_value("Customer", row["name"], "default_price_list")
	]
	summary = {"dry_run": dry_run, "rows_in_csv": len(rows), "restorable": len(restorable)}
	if dry_run:
		return summary

	# Raw SQL on purpose: validate_no_branch_price_list would reject the restore.
	for row in restorable:
		frappe.db.sql(
			"""
			update `tabCustomer` set default_price_list = %(pl)s
			where name = %(name)s and coalesce(default_price_list, '') = ''
			""",
			{"pl": row["default_price_list"], "name": row["name"]},
		)
	frappe.db.commit()
	frappe.clear_cache(doctype="Customer")
	summary["restored"] = len(restorable)
	return summary
