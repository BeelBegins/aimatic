"""Desk API for the retail finance setup console."""

import frappe
from frappe import _
from frappe.utils import now_datetime

from aimatic.retail_finance_setup.checks import run_checks
from aimatic.retail_finance_setup.registry import REGISTRY_VERSION, get_capabilities

ALLOWED_ROLES = {"Accounts User", "Accounts Manager", "System Manager"}


def _require_access():
	if not ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You need an Accounts or System Manager role to view finance readiness."), frappe.PermissionError)


def _resolve_company(company=None):
	if company:
		if not frappe.db.exists("Company", company) or not frappe.has_permission("Company", doc=company, ptype="read"):
			frappe.throw(_("Company {0} is unavailable or not permitted.").format(frappe.bold(company)), frappe.PermissionError)
		return company

	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company")
	if company and frappe.has_permission("Company", doc=company, ptype="read"):
		return company
	companies = frappe.get_list("Company", fields=["name"], limit=1)
	if not companies:
		frappe.throw(_("No permitted Company is available."))
	return companies[0].name


@frappe.whitelist()
def get_capability_registry():
	_require_access()
	return {"registry_version": REGISTRY_VERSION, "capabilities": get_capabilities()}


@frappe.whitelist()
def get_readiness(company=None):
	_require_access()
	company = _resolve_company(company)
	checks = run_checks(company)
	capabilities = get_capabilities()

	counts = {"pass": 0, "warning": 0, "blocked": 0, "partial": 0, "planned": 0, "info": 0}
	for capability in capabilities:
		check = checks.get(capability.get("check_key"))
		if capability["implementation_status"] in {"missing", "separate"}:
			capability["readiness_status"] = "planned"
			capability["readiness_message"] = "Tracked as separate implementation work."
		elif capability["implementation_status"] == "partial" and (not check or check["status"] == "pass"):
			capability["readiness_status"] = "partial"
			capability["readiness_message"] = "The available foundation passes, but this capability remains explicitly partial."
			if check:
				capability["check_details"] = check["details"]
		elif check:
			capability["readiness_status"] = check["status"]
			capability["readiness_message"] = check["message"]
			capability["check_details"] = check["details"]
		else:
			capability["readiness_status"] = "info"
			capability["readiness_message"] = "Capability is registered; no automated readiness check is defined."
		counts[capability["readiness_status"]] = counts.get(capability["readiness_status"], 0) + 1

	critical_blocks = [capability["id"] for capability in capabilities if capability["critical"] and capability["readiness_status"] == "blocked"]
	return {
		"registry_version": REGISTRY_VERSION,
		"generated_at": now_datetime(),
		"company": company,
		"forward_operations_ready": not critical_blocks,
		"critical_blocks": critical_blocks,
		"counts": counts,
		"checks": list(checks.values()),
		"capabilities": capabilities,
		"cutover_note": "Existing supplier, inventory, and accounting openings are the accepted baseline. No unavailable history is reconstructed; reporting proceeds forward.",
	}
