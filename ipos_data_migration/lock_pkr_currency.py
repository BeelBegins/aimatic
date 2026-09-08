"""Force PKR on Price Lists, Item Prices, and Currency masters (siezal mock).

S2–S7 branch Price Lists were created with INR; S7 import copied that onto
Item Price. Company and Global Defaults are PKR. Live `szl` has the same
INR Price Lists — run this there only after backup, before S7 prices.

    ns={}
    path="/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/lock_pkr_currency.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import frappe

TARGET_SITE = "siezal"
REQUIRED_CURRENCY = "PKR"


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing: expected {TARGET_SITE}, got {site}")


def snapshot():
	return {
		"price_lists": frappe.db.sql(
			"select currency, count(*) from `tabPrice List` group by currency"
		),
		"item_prices": frappe.db.sql(
			"select currency, count(*) from `tabItem Price` group by currency"
		),
		"enabled_currencies": frappe.get_all("Currency", filters={"enabled": 1}, pluck="name"),
		"company": frappe.get_all("Company", fields=["name", "default_currency"]),
		"global": frappe.db.get_single_value("Global Defaults", "default_currency"),
	}


def main(apply: bool = False):
	_assert_site()
	before = snapshot()
	print({"site": TARGET_SITE, "apply": apply, "before": before})
	if not apply:
		print("DRY_RUN — no currency changes.")
		return before
	frappe.db.sql(
		"update `tabPrice List` set currency=%s where ifnull(currency,'')!=%s",
		(REQUIRED_CURRENCY, REQUIRED_CURRENCY),
	)
	frappe.db.sql(
		"update `tabItem Price` set currency=%s where ifnull(currency,'')!=%s",
		(REQUIRED_CURRENCY, REQUIRED_CURRENCY),
	)
	disabled = []
	for name in frappe.get_all("Currency", filters={"enabled": 1}, pluck="name"):
		if name == REQUIRED_CURRENCY:
			continue
		frappe.db.set_value("Currency", name, "enabled", 0)
		disabled.append(name)
	frappe.db.commit()
	after = snapshot()
	print({"disabled": disabled, "after": after})
	if any(cur != REQUIRED_CURRENCY for cur, _cnt in after["item_prices"]):
		frappe.throw(f"Item Price still has non-{REQUIRED_CURRENCY}: {after['item_prices']}")
	if any(cur != REQUIRED_CURRENCY for cur, _cnt in after["price_lists"]):
		frappe.throw(f"Price List still has non-{REQUIRED_CURRENCY}: {after['price_lists']}")
	if REQUIRED_CURRENCY not in after["enabled_currencies"] or len(after["enabled_currencies"]) != 1:
		frappe.throw(f"Enabled currencies should be only {REQUIRED_CURRENCY}: {after['enabled_currencies']}")
	return after
