"""Enable S5/S6 POS profiles after unique production FBR POS IDs.

Does not create profiles or change cash accounts. Refuses if FBR is
disabled, if pos_id reuses S1/S4/S7 or the placeholders, or if another
branch already uses the same pos_id. Hard-targets ``szl``. Dry-run default.

    ns = {}
    path = "/home/nabeel/frappe-bench/apps/aimatic/ipos_data_migration/enable_s5_s6_pos.py"
    exec(compile(open(path).read(), path, "exec"), ns)
    ns["main"]()
    ns["main"](apply=True)
"""

from __future__ import annotations

import frappe

TARGET_SITE = "szl"
FORBIDDEN_POS_IDS = {"176213", "184234", "196711", "100500", "100600", ""}
BRANCHES = (
	{
		"settings": "Siezal Supermarket-S6 - Khalid Block",
		"branch": "S6 - Khalid Block",
		"branch_code": "1006",
		"profiles": ("S6 Counter 1", "S6 FP1"),
		"cash": "1114 - Cash in Hand - S6KB - SSM",
	},
	{
		"settings": "Siezal Supermarket-S5 - Sector C",
		"branch": "S5 - Sector C",
		"branch_code": "1005",
		"profiles": ("S5 Counter 1", "S5 FP1", "S5 HD"),
		"cash": "1115 - Cash in Hand - S5SC - SSM",
	},
)
PROTECTED_PROFILES = {
	"S1 Counter 1",
	"S4 Counter 1",
	"S4 Food Panda",
	"S7 Counter 1",
	"S7 Food Panda",
}


def _assert_site():
	site = getattr(frappe.local, "site", None) or ""
	if site != TARGET_SITE:
		frappe.throw(f"Refusing POS enable: expected {TARGET_SITE}, got {site}")


def plan():
	rows = frappe.get_all(
		"FBR Integration Settings",
		fields=["name", "branch", "pos_id", "branch_code", "enabled"],
	)
	by_name = {row.name: row for row in rows}
	pos_owners = {}
	for row in rows:
		pid = str(row.pos_id or "").strip()
		if pid:
			pos_owners.setdefault(pid, []).append(row.name)
	checks = []
	for spec in BRANCHES:
		fbr = by_name.get(spec["settings"])
		if not fbr:
			checks.append({**spec, "ok": False, "reason": "missing_fbr"})
			continue
		pid = str(fbr.pos_id or "").strip()
		reasons = []
		if not int(fbr.enabled or 0):
			reasons.append("fbr_disabled")
		if pid in FORBIDDEN_POS_IDS:
			reasons.append(f"forbidden_pos_id:{pid or 'blank'}")
		if fbr.branch != spec["branch"]:
			reasons.append(f"branch_mismatch:{fbr.branch}")
		if str(fbr.branch_code or "") != spec["branch_code"]:
			reasons.append(f"branch_code_mismatch:{fbr.branch_code}")
		owners = pos_owners.get(pid) or []
		if len(owners) != 1 or owners[0] != spec["settings"]:
			reasons.append(f"pos_id_not_unique:{pid}:{owners}")
		profiles = []
		for name in spec["profiles"]:
			if name in PROTECTED_PROFILES:
				reasons.append(f"protected_profile:{name}")
				continue
			doc = frappe.db.get_value(
				"POS Profile",
				name,
				["disabled", "account_for_change_amount", "branch"],
				as_dict=True,
			)
			if not doc:
				reasons.append(f"missing_profile:{name}")
				continue
			if doc.branch != spec["branch"]:
				reasons.append(f"profile_branch:{name}:{doc.branch}")
			if doc.account_for_change_amount != spec["cash"]:
				reasons.append(f"change_account:{name}:{doc.account_for_change_amount}")
			profiles.append({"name": name, "disabled": int(doc.disabled or 0)})
		checks.append(
			{
				"settings": spec["settings"],
				"pos_id": pid,
				"enabled": int(fbr.enabled or 0),
				"ok": not reasons,
				"reasons": reasons,
				"profiles": profiles,
			}
		)
	protected = {
		name: int(frappe.db.get_value("POS Profile", name, "disabled") or 0)
		for name in sorted(PROTECTED_PROFILES)
		if frappe.db.exists("POS Profile", name)
	}
	return {"checks": checks, "protected_disabled": protected}


def main(apply: bool = False):
	_assert_site()
	snapshot = plan()
	print({"site": TARGET_SITE, "apply": apply, **snapshot})
	if not apply:
		print("DRY_RUN — POS profiles unchanged.")
		return snapshot
	if any(not row["ok"] for row in snapshot["checks"]):
		frappe.throw("Refusing POS enable: FBR/profile gates failed")
	before_protected = snapshot["protected_disabled"]
	for row in snapshot["checks"]:
		for profile in row["profiles"]:
			frappe.db.set_value("POS Profile", profile["name"], "disabled", 0)
	frappe.db.commit()
	after_protected = {
		name: int(frappe.db.get_value("POS Profile", name, "disabled") or 0)
		for name in before_protected
	}
	if after_protected != before_protected:
		frappe.throw(f"Protected POS profiles changed: {before_protected} -> {after_protected}")
	after = plan()
	print({"after": after})
	return after
