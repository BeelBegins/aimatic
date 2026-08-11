"""One-off: ensure every Tax Withholding Category on szl has Filers and
Non-Filers rate rows covering 2020-01-01..2030-12-31.

ERPNext matches rates by posting_date AND tax_withholding_group. Categories
that only had a blank-group row (Exempt, WHT 5%) or only Filers rows caused
Purchase Invoice returns (and new PIs with Apply Tax Withholding) to throw
"No Tax Withholding data found for the current posting date." for Non-Filers
suppliers and for Filers on blank-group categories.

Locked to site szl. Idempotent: skips groups that already have a covering row.
Blank-group rows are removed after Filers/Non-Filers rows exist for that rate.

Run: bench --site szl execute aimatic.fix_szl_wht_rate_groups.run
"""

from __future__ import annotations

import frappe
from frappe.utils import cstr, flt, getdate

TARGET_SITE = "szl"
GROUPS = ("Filers", "Non-Filers")
FROM_DATE = "2020-01-01"
TO_DATE = "2030-12-31"
# Any posting date in the live retail window must be covered.
COVER_DATE = "2026-08-07"


def _canonical_rate(doc) -> float:
	"""Prefer an existing rate value on the category; default 0 for Exempt."""
	if doc.rates:
		return flt(doc.rates[0].tax_withholding_rate)
	if (doc.name or "").strip().lower() == "exempt":
		return 0.0
	label = (doc.category_name or doc.name or "").replace("WHT", "").replace("%", "").strip()
	try:
		return float(label)
	except ValueError:
		frappe.throw(f"Cannot determine withholding rate for category {doc.name}")


def _row_covers_today(row) -> bool:
	return getdate(row.from_date) <= getdate(COVER_DATE) <= getdate(row.to_date)


def _has_group_covering_today(doc, group: str) -> bool:
	return any(cstr(r.tax_withholding_group) == group and _row_covers_today(r) for r in doc.rates)


def _snapshot(doc):
	return [
		{
			"group": cstr(r.tax_withholding_group) or "(blank)",
			"rate": r.tax_withholding_rate,
			"from": str(r.from_date),
			"to": str(r.to_date),
		}
		for r in doc.rates
	]


def run():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(
			f"This script is locked to site '{TARGET_SITE}', but current site is '{frappe.local.site}'."
		)

	for group in GROUPS:
		if not frappe.db.exists("Tax Withholding Group", group):
			frappe.throw(f"Required Tax Withholding Group missing: {group}")

	categories = frappe.get_all("Tax Withholding Category", pluck="name")
	changed = []

	for name in sorted(categories):
		doc = frappe.get_doc("Tax Withholding Category", name)
		rate = _canonical_rate(doc)
		before = _snapshot(doc)
		dirty = False

		for group in GROUPS:
			if _has_group_covering_today(doc, group):
				continue
			doc.append(
				"rates",
				{
					"from_date": FROM_DATE,
					"to_date": TO_DATE,
					"tax_withholding_group": group,
					"tax_withholding_rate": rate,
				},
			)
			dirty = True

		if all(_has_group_covering_today(doc, g) for g in GROUPS):
			blank_rows = [r for r in doc.rates if not cstr(r.tax_withholding_group)]
			if blank_rows:
				kept = [r for r in doc.rates if cstr(r.tax_withholding_group)]
				doc.set("rates", [])
				for r in kept:
					doc.append(
						"rates",
						{
							"from_date": r.from_date,
							"to_date": r.to_date,
							"tax_withholding_group": r.tax_withholding_group,
							"tax_withholding_rate": r.tax_withholding_rate,
							"single_threshold": r.single_threshold,
							"cumulative_threshold": r.cumulative_threshold,
						},
					)
				dirty = True

		if not dirty:
			print(f"OK (unchanged): {name} rate={rate} before={before}")
			continue

		doc.save(ignore_permissions=True)
		after = _snapshot(doc)
		changed.append(name)
		print(f"UPDATED: {name} rate={rate}")
		print(f"  before: {before}")
		print(f"  after:  {after}")

	frappe.db.commit()
	print(f"Done. Updated {len(changed)} categories: {changed}")
	return {"updated": changed, "categories": len(categories)}
