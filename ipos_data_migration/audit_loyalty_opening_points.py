"""Read-only audit: customers with loyalty_program vs positive Loyalty Point Entry balance.

Run on a site (does not write), from the bench root:

  bench --site szl console
  >>> exec(
  ...     open(
  ...         "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/audit_loyalty_opening_points.py"
  ...     ).read()
  ... )
  >>> execute()

Prints enrolled-with-zero-ledger counts and a sample of names for follow-up.
Repair of missing opening LPEs is intentionally separate and requires approval.
"""

from __future__ import annotations

import frappe


def execute(limit: int = 50):
	enrolled = frappe.db.sql(
		"""
        SELECT COUNT(*) FROM `tabCustomer`
        WHERE IFNULL(loyalty_program, '') != '' AND IFNULL(disabled, 0) = 0
        """
	)[0][0]

	with_positive = frappe.db.sql(
		"""
        SELECT COUNT(DISTINCT customer) FROM `tabLoyalty Point Entry`
        WHERE loyalty_points > 0
        """
	)[0][0]

	enrolled_no_points = frappe.db.sql(
		"""
        SELECT c.name, c.customer_name, c.mobile_no, c.loyalty_program
        FROM `tabCustomer` c
        WHERE IFNULL(c.loyalty_program, '') != ''
          AND IFNULL(c.disabled, 0) = 0
          AND NOT EXISTS (
            SELECT 1 FROM `tabLoyalty Point Entry` lpe
            WHERE lpe.customer = c.name AND lpe.loyalty_points > 0
          )
        ORDER BY c.modified DESC
        LIMIT %(limit)s
        """,
		{"limit": int(limit)},
		as_dict=True,
	)

	enrolled_no_points_count = frappe.db.sql(
		"""
        SELECT COUNT(*) FROM `tabCustomer` c
        WHERE IFNULL(c.loyalty_program, '') != ''
          AND IFNULL(c.disabled, 0) = 0
          AND NOT EXISTS (
            SELECT 1 FROM `tabLoyalty Point Entry` lpe
            WHERE lpe.customer = c.name AND lpe.loyalty_points > 0
          )
        """
	)[0][0]

	sample_points = frappe.db.sql(
		"""
        SELECT customer, SUM(loyalty_points) AS points
        FROM `tabLoyalty Point Entry`
        WHERE loyalty_points > 0
        GROUP BY customer
        ORDER BY points DESC
        LIMIT 5
        """,
		as_dict=True,
	)

	print("Loyalty audit (read-only)")
	print(f"  enrolled customers: {enrolled}")
	print(f"  customers with positive LPE rows: {with_positive}")
	print(f"  enrolled with no positive LPE: {enrolled_no_points_count}")
	print(f"  sample enrolled-no-points (up to {limit}):")
	for row in enrolled_no_points:
		print(f"    {row.name} | {row.customer_name} | {row.mobile_no} | {row.loyalty_program}")
	print("  top positive balances:")
	for row in sample_points:
		print(f"    {row.customer} | {row.points}")

	return {
		"enrolled": enrolled,
		"with_positive_lpe": with_positive,
		"enrolled_no_positive_lpe": enrolled_no_points_count,
		"sample": enrolled_no_points,
	}
