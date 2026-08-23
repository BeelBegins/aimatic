"""Assign the current SZL operating users to S1 - Ghouri Town VIP.

Run from ``bench --site szl console``::

    exec(open("apps/aimatic/ipos_data_migration/assign_szl_s1_users.py").read())
    run()

Keep ``DRY_RUN`` enabled for the first pass.  This script intentionally leaves
the built-in Administrator, Guest, and Nabeel's administrator account without a
Branch restriction.
"""

import frappe

TARGET_SITE = "szl"
BRANCH = "S1 - Ghouri Town VIP"
DRY_RUN = True

TARGET_USERS = (
	"abasit@aimatic.tech",
	"foodpanda@aimatic.tech",
	"mamir@aimatic.tech",
	"meomair@gmail.com",
	"mwaqas@aimatic.tech",
	"mzaman@aimatic.tech",
	"sayyam@aimatic.tech",
	"smir@aimatic.tech",
)

EXCLUDED_USERS = {
	"Administrator",
	"Guest",
	"nabeelmehmood448@gmail.com",
}


def _validate_scope():
	if frappe.local.site != TARGET_SITE:
		frappe.throw(f"This script is locked to site {TARGET_SITE}, not {frappe.local.site}.")

	if not frappe.db.exists("Branch", BRANCH):
		frappe.throw(f"Branch {BRANCH} does not exist on {TARGET_SITE}.")

	missing = [user for user in TARGET_USERS if not frappe.db.exists("User", user)]
	if missing:
		frappe.throw(f"Expected SZL users are missing: {', '.join(missing)}")

	unexpected = frappe.get_all(
		"User",
		filters={
			"enabled": 1,
			"user_type": "System User",
			"name": ["not in", [*TARGET_USERS, *EXCLUDED_USERS]],
		},
		pluck="name",
	)
	if unexpected:
		frappe.throw(
			"Active System Users outside the locked assignment list: " + ", ".join(sorted(unexpected))
		)


def run():
	_validate_scope()
	created = []
	updated = []
	already_correct = []

	for user in TARGET_USERS:
		other_branches = frappe.get_all(
			"User Permission",
			filters={"user": user, "allow": "Branch", "for_value": ["!=", BRANCH]},
			pluck="for_value",
		)
		if other_branches:
			frappe.throw(
				f"{user} already has other Branch permissions: {', '.join(other_branches)}. "
				"Review them explicitly before changing access."
			)

		permission_name = frappe.db.get_value(
			"User Permission",
			{"user": user, "allow": "Branch", "for_value": BRANCH},
			"name",
		)
		if not permission_name:
			created.append(user)
			if not DRY_RUN:
				frappe.get_doc(
					{
						"doctype": "User Permission",
						"user": user,
						"allow": "Branch",
						"for_value": BRANCH,
						"is_default": 1,
						"apply_to_all_doctypes": 1,
					}
				).insert(ignore_permissions=True)
			continue

		current = frappe.db.get_value(
			"User Permission",
			permission_name,
			["is_default", "apply_to_all_doctypes"],
			as_dict=True,
		)
		if current.is_default and current.apply_to_all_doctypes:
			already_correct.append(user)
			continue

		updated.append(user)
		if not DRY_RUN:
			frappe.db.set_value(
				"User Permission",
				permission_name,
				{"is_default": 1, "apply_to_all_doctypes": 1},
			)

	if not DRY_RUN:
		# User.custom_branch is only a visible mirror; User Permission remains
		# the source of truth.  Set it here as well so updates and inserts both
		# leave the User list immediately consistent.
		for user in TARGET_USERS:
			frappe.db.set_value("User", user, "custom_branch", BRANCH)
		frappe.db.commit()

	print(f"DRY_RUN={DRY_RUN}")
	print(f"Created ({len(created)}): {', '.join(created) or '-'}")
	print(f"Updated ({len(updated)}): {', '.join(updated) or '-'}")
	print(f"Already correct ({len(already_correct)}): {', '.join(already_correct) or '-'}")
	print(f"Excluded: {', '.join(sorted(EXCLUDED_USERS))}")
