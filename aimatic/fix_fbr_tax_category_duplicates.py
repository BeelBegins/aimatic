"""Remap duplicate FBR Tax Categories to standards, then disable the duplicates.

Duplicates (legacy / fixture-synced extras):
  Standard GST 18%              -> Goods at standard rate (default)
  3rd Schedule Goods @ 18%      -> 3rd Schedule Goods
  Goods at 25%                  -> Goods as per SRO.297(I)/2023
  Exempted                      -> Exempt goods
  Goods as per SRO.297(|)/2023  -> Goods as per SRO.297(I)/2023  (typo twin)

Run per site:
  bench --site <site> execute aimatic.fix_fbr_tax_category_duplicates.run
"""

from __future__ import annotations

import frappe
from frappe.utils import flt, now

REMAP = {
	"Standard GST 18%": "Goods at standard rate (default)",
	"3rd Schedule Goods @ 18%": "3rd Schedule Goods",
	"Goods at 25%": "Goods as per SRO.297(I)/2023",
	"Exempted": "Exempt goods",
	"Goods as per SRO.297(|)/2023": "Goods as per SRO.297(I)/2023",
}

# Child/parent tables that may still point at the duplicate names.
LINK_COLUMNS = (
	("tabItem", "custom_fbr_tax_category", True),  # also sync custom_fbr_tax_rate
	("tabPOS Invoice Item", "custom_fbr_tax_category", False),
)


def _table_has_column(table: str, column: str) -> bool:
	return bool(
		frappe.db.sql(
			"""
			SELECT 1 FROM information_schema.COLUMNS
			WHERE TABLE_SCHEMA = DATABASE()
			  AND TABLE_NAME = %s
			  AND COLUMN_NAME = %s
			LIMIT 1
			""",
			(table, column),
		)
	)


def run():
	for old, new in REMAP.items():
		if not frappe.db.exists("FBR Tax Category", new):
			frappe.throw(f"Standard category missing on {frappe.local.site}: {new}")

	summary = {"site": frappe.local.site, "remapped": {}, "disabled": []}

	for old, new in REMAP.items():
		rate = flt(frappe.db.get_value("FBR Tax Category", new, "tax_rate"))
		moved = {}
		for table, column, sync_rate in LINK_COLUMNS:
			if not _table_has_column(table, column):
				continue
			count = frappe.db.sql(
				f"SELECT COUNT(*) FROM `{table}` WHERE `{column}` = %s",
				(old,),
			)[0][0]
			if not count:
				moved[table] = 0
				continue
			if sync_rate and _table_has_column(table, "custom_fbr_tax_rate"):
				frappe.db.sql(
					f"""
					UPDATE `{table}`
					SET `{column}` = %s,
					    custom_fbr_tax_rate = %s,
					    modified = %s,
					    modified_by = %s
					WHERE `{column}` = %s
					""",
					(new, rate, now(), frappe.session.user or "Administrator", old),
				)
			else:
				frappe.db.sql(
					f"""
					UPDATE `{table}`
					SET `{column}` = %s,
					    modified = %s,
					    modified_by = %s
					WHERE `{column}` = %s
					""",
					(new, now(), frappe.session.user or "Administrator", old),
				)
			moved[table] = int(count)
		summary["remapped"][old] = {"to": new, "rows": moved}

		if frappe.db.exists("FBR Tax Category", old):
			frappe.db.set_value(
				"FBR Tax Category",
				old,
				{"enabled": 0},
				update_modified=True,
			)
			summary["disabled"].append(old)

	frappe.db.commit()
	print(frappe.as_json(summary, indent=2))
	return summary
