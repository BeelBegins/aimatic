"""Phase 4b: execute a cataloged ERPNext Query/Script Report - the missing half of
report_registry.discover_frappe_reports(), which only ever catalogued reports, never
ran them. Two tools:
  - list_frappe_reports: keyword search over the same discovered-reports catalogue
    report_registry.py already builds, so the model can find a report name before
    trying to run it (there is no way for it to guess a valid name otherwise).
  - run_frappe_report: executes ONE cataloged report via frappe.desk.query_report.run,
    the same server function the Report View itself calls, so that report's own
    permission checks (doc.is_permitted(), frappe.has_permission(ref_doctype, "report"))
    apply unchanged. A report is only runnable if it's in the discovered catalogue
    (Query/Script Report, not disabled, module in Accounts/Buying/Selling/Stock/
    Aimatic) - an arbitrary report name is rejected before any execution is attempted.
Company scoping is force-applied the same way dynamic_report.py forces it: if the
caller's filters dict has a "company" key (most ERPNext reports accept one), it is
always overwritten with the user's own resolved default company, never left to
whatever the model supplied.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
import frappe.desk.query_report

from aimatic.ai.tools import _resolve_company, _resolve_branch_filter
from aimatic.ai.report_registry import discover_frappe_reports

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 200


def list_frappe_reports(keyword: str | None = None, limit: int = 20) -> dict:
    """Search the catalogue of runnable Frappe reports (Query/Script Report from
    Accounts/Buying/Selling/Stock/Aimatic modules) by keyword overlap against the
    report's own name/description. Call this BEFORE run_frappe_report to find a
    valid report_name - run_frappe_report rejects any name not returned here."""
    limit = max(1, min(int(limit or 20), 50))
    registry = discover_frappe_reports()
    reports = list(registry.values())

    if keyword and keyword.strip():
        words = {w for w in keyword.lower().split() if len(w) >= 3}
        scored = []
        for ds in reports:
            hay = f"{ds.name} {ds.description}".lower()
            overlap = sum(1 for w in words if w in hay)
            if overlap:
                scored.append((overlap, ds))
        scored.sort(key=lambda x: -x[0])
        reports = [ds for _score, ds in scored]

    return {
        "reports": [
            {"report_name": ds.name, "description": ds.description}
            for ds in reports[:limit]
        ],
        "total_available": len(registry),
    }


def run_frappe_report(report_name: str, filters: dict | str | None = None, limit: int = 100) -> dict:
    """Executes one report found via list_frappe_reports. Only a report present in
    the discovered catalogue can run - discover_frappe_reports() is the single
    source of truth for what's allowed, so a new/renamed/disabled report is picked
    up or dropped automatically without a second list to maintain. Company is
    always forced to the caller's own default company when the filters dict
    carries that key; frappe.desk.query_report.run applies the report's own
    permission checks (ref_doctype "report" permission, doc.is_permitted())
    unchanged, and any frappe.PermissionError it raises propagates rather than
    being swallowed into an {"error": ...} dict - same convention as
    dynamic_report.run_dynamic_report's frappe.has_permission check.

    Branch scoping: unlike every purpose-built tool in this module, an arbitrary
    cataloged report's own SQL/script is not guaranteed to filter rows by the
    caller's Branch User Permission the way frappe.db.get_list does - many core
    ERPNext Query/Script Reports run raw SQL with no per-row permission check at
    all. So this refuses to run any report at all for a branch-restricted caller
    (one whose _resolve_branch_filter is not None, i.e. they see a strict subset
    of the company's branches) rather than risk leaking another branch's data
    through a report this app never wrote and can't guarantee is branch-aware."""
    registry = discover_frappe_reports()
    key = f"report:{report_name}"
    if key not in registry:
        return {
            "error": f"Report not available: {report_name!r}. Call list_frappe_reports first to find a valid report_name.",
        }

    company = _resolve_company()
    if _resolve_branch_filter(company, None) is not None:
        return {
            "error": (
                "Your account is restricted to specific branches, and this report cannot "
                "guarantee it only returns data for those branches. Ask a purpose-built "
                "tool or run_analytics_query instead - both correctly scope to your "
                "visible branches."
            ),
        }

    if isinstance(filters, str):
        try:
            filters = json.loads(filters) if filters.strip() else {}
        except (TypeError, ValueError):
            return {"error": f"Could not parse filters JSON: {filters!r}"}
    filters = dict(filters or {})

    # Force-applied regardless of whether the model set it - never left to an
    # LLM-supplied company value, same invariant dynamic_report.py enforces.
    filters["company"] = company

    limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))

    try:
        result = frappe.desk.query_report.run(report_name, filters=filters, ignore_prepared_report=True)
    except frappe.PermissionError:
        raise
    except Exception as e:
        return {"error": f"Report execution failed: {e}"}

    columns = [
        {
            "fieldname": c.get("fieldname"),
            "label": c.get("label"),
            "fieldtype": c.get("fieldtype"),
            "options": c.get("options"),
        }
        for c in (result.get("columns") or [])
    ]
    rows = result.get("result") or []
    total_row_count = len(rows)
    rows = rows[:limit]

    return {
        "report_name": report_name,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "total_row_count": total_row_count,
        "filters_applied": {k: str(v) for k, v in filters.items()},
    }


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_frappe_reports",
            "description": (
                "Search the catalogue of existing ERPNext reports (Query/Script Reports from "
                "Accounts/Buying/Selling/Stock modules, plus this app's own custom reports) by "
                "keyword. Call this BEFORE run_frappe_report to find a valid report_name - there "
                "is no other way to know one. Only use this when no purpose-built tool answers "
                "the question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Keyword(s) to search report names/descriptions for, e.g. \"stock balance\" or \"accounts receivable\"."},
                    "limit": {"type": "integer", "description": "Max reports to return, default 20, max 50."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_frappe_report",
            "description": (
                "Execute one existing ERPNext report by exact name (found via list_frappe_reports "
                "first) and return its rows. Company scoping is applied automatically. Use this "
                "for questions best answered by an existing standard ERPNext report (e.g. Accounts "
                "Receivable, Stock Balance, Sales Analytics) rather than a purpose-built tool or "
                "run_dynamic_report."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "report_name": {"type": "string", "description": "Exact report name, as returned by list_frappe_reports."},
                    "filters": {"type": "object", "description": "Filter values to pass to the report, e.g. {\"from_date\": \"2026-07-01\", \"to_date\": \"2026-07-31\"}. Company is always forced server-side regardless of this."},
                    "limit": {"type": "integer", "description": "Max rows to return, default 100, max 200."},
                },
                "required": ["report_name"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "list_frappe_reports": list_frappe_reports,
    "run_frappe_report": run_frappe_report,
}
