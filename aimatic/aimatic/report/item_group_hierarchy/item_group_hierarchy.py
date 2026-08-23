from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data, get_message()


def get_columns():
	return [
		{
			"label": _("Item Group"),
			"fieldname": "item_group",
			"fieldtype": "Link",
			"options": "Item Group",
			"width": 220,
		},
		{"label": _("Item Group Level 1"), "fieldname": "level_1", "fieldtype": "Data", "width": 150},
		{"label": _("Item Group Level 2"), "fieldname": "level_2", "fieldtype": "Data", "width": 150},
		{"label": _("Item Group Level 3"), "fieldname": "level_3", "fieldtype": "Data", "width": 150},
		{"label": _("Item Group Level 4"), "fieldname": "level_4", "fieldtype": "Data", "width": 150},
		{"label": _("Is Group"), "fieldname": "is_group", "fieldtype": "Data", "width": 80},
		{
			"label": _("Loyalty Rate Configured"),
			"fieldname": "loyalty_rate_configured",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Loyalty Points per Rs 100"),
			"fieldname": "loyalty_points_rate",
			"fieldtype": "Float",
			"width": 150,
		},
		{
			"label": _("Effective Loyalty Points per Rs 100"),
			"fieldname": "effective_loyalty_points_rate",
			"fieldtype": "Float",
			"width": 180,
		},
	]


def get_data(filters):
	groups = frappe.get_all(
		"Item Group",
		fields=[
			"name",
			"parent_item_group",
			"is_group",
			"custom_loyalty_rate_configured",
			"custom_loyalty_points_rate",
		],
		order_by="lft asc",
	)
	by_name = {group.name: group for group in groups}
	chain_cache: dict[str, list[str]] = {}

	def get_chain(name: str) -> list[str]:
		# Root-to-self ordered ancestor chain, excluding the tree's own root
		# sentinel (e.g. "All Item Groups") - that node has no parent and its
		# chain resolves to [], which also makes it skip becoming a report row.
		if name not in chain_cache:
			parent = by_name.get(name, {}).get("parent_item_group")
			chain_cache[name] = [*get_chain(parent), name] if parent else []
		return chain_cache[name]

	def get_effective_rate(chain: list[str]) -> float:
		# Mirrors aimatic.loyalty.item_group_rate.get_item_group_loyalty_rate:
		# nearest configured ancestor wins, self first.
		for group_name in reversed(chain):
			group = by_name.get(group_name)
			if group and cint(group.custom_loyalty_rate_configured):
				return flt(group.custom_loyalty_points_rate)
		return 0.0

	include_groups = cint(filters.get("include_groups", 1))

	rows = []
	for group in groups:
		if not include_groups and cint(group.is_group):
			continue

		chain = get_chain(group.name)
		if not chain:
			continue

		levels = (chain + ["", "", "", ""])[:4]
		if len(chain) > 4:
			levels[3] = " / ".join(chain[3:])

		configured = cint(group.custom_loyalty_rate_configured)
		rows.append(
			{
				"item_group": group.name,
				"level_1": levels[0],
				"level_2": levels[1],
				"level_3": levels[2],
				"level_4": levels[3],
				"is_group": _("Yes") if cint(group.is_group) else _("No"),
				"loyalty_rate_configured": _("Yes") if configured else _("No"),
				"loyalty_points_rate": flt(group.custom_loyalty_points_rate) if configured else None,
				"effective_loyalty_points_rate": get_effective_rate(chain),
			}
		)

	return rows


def get_message():
	meta = frappe.get_meta("Item Group")
	configured_description = meta.get_field("custom_loyalty_rate_configured").description
	rate_description = meta.get_field("custom_loyalty_points_rate").description
	effective_description = _(
		"The rate actually applied to items in this group, after inheriting from the "
		"nearest ancestor Item Group that has an explicit rate configured."
	)
	return (
		'<div class="text-muted small">'
		f"<b>{_('Loyalty Rate Configured')}</b>: {configured_description}<br>"
		f"<b>{_('Loyalty Points per Rs 100')}</b>: {rate_description}<br>"
		f"<b>{_('Effective Loyalty Points per Rs 100')}</b>: {effective_description}"
		"</div>"
	)
