from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, today

# All three sites currently have POS Settings.invoice_type == "POS Invoice", so every
# query below targets `tabPOS Invoice` directly. This is a single global per-site
# toggle, not guaranteed to stay "POS Invoice" forever - if a site's invoice_type is
# ever switched to "Sales Invoice", every query in this module needs updating too, or
# the dashboard silently goes blind for that site.

_ALLOWED_ROLES = {"System Manager", "Sales Manager", "Accounts Manager", "POS Supervisor"}

# Fixed lookback for the trend chart, independent of the KPI date-range filter - a
# single-day filter (e.g. "Today") would otherwise produce a one-point "trend".
_TREND_DAYS = 14


def _check_dashboard_role():
	if not (set(frappe.get_roles()) & _ALLOWED_ROLES):
		frappe.throw(_("Not permitted to view the sales dashboard."), frappe.PermissionError)


def _resolve_company(company: str | None) -> str:
	company = company or frappe.defaults.get_user_default("Company") or frappe.defaults.get_default("company")
	if not company:
		frappe.throw(_("Company is required."))
	if not frappe.db.exists("Company", company):
		frappe.throw(_("Company {0} was not found.").format(company))
	return company


def _resolve_context(company: str | None, branch: str | None = None):
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	_check_dashboard_role()
	if not frappe.has_permission("POS Invoice", ptype="read"):
		frappe.throw(_("Not permitted to view sales data."), frappe.PermissionError)
	company = _resolve_company(company)
	if branch and not frappe.has_permission("Branch", ptype="read", doc=branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)
	currency = frappe.get_cached_value("Company", company, "default_currency")
	return company, currency


def _get_date_range(date_from: str | None, date_to: str | None):
	date_to = getdate(date_to) if date_to else getdate(today())
	date_from = getdate(date_from) if date_from else date_to
	if date_from > date_to:
		date_from, date_to = date_to, date_from
	return date_from, date_to


def _resolve_branch_filter(company: str, branch: str | None) -> list[str] | None:
	"""None means "no branch filter" - full company total, including branch-less/
	Unassigned rows. A list means "restrict to exactly these branches" - either
	because the caller explicitly picked one branch, or because the current user's
	own Branch User Permissions only grant visibility into a subset of this
	company's branches (a deliberately conservative default: a restricted user also
	doesn't see company-wide Unassigned rows that aren't tied to their own branch).
	An empty list means the user is restricted down to zero visible branches."""
	if branch:
		return [branch]
	visible = frappe.get_all("Branch", filters={"company": company}, pluck="name")
	total_count = frappe.db.count("Branch", {"company": company})
	if len(visible) < total_count:
		return visible
	return None


def _branch_scope_clause(branch_filter: list[str] | None) -> str:
	return "AND COALESCE(pi.branch, pp.branch) IN %(branch_names)s" if branch_filter is not None else ""


@frappe.whitelist()
def get_dashboard_summary(
	company: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	branch: str | None = None,
):
	"""The one cheap, always-loaded call: date-range KPIs + trend + branch comparison
	for the selected range, plus today's branch -> POS Profile grid (always "today",
	independent of the date-range filter) and currently open shifts with their live
	running totals - a shift that hasn't closed yet still contributes its running
	total here, it is never invisible just because no POS Closing Entry exists yet."""
	company, currency = _resolve_context(company, branch)
	date_from, date_to = _get_date_range(date_from, date_to)
	branch_filter = _resolve_branch_filter(company, branch)

	kpis = _get_range_kpis(company, date_from, date_to, branch_filter)
	branch_comparison = _get_branch_comparison(company, date_from, date_to, branch_filter)
	if branch_comparison:
		kpis["top_branch"] = branch_comparison[0]["branch"]
		kpis["top_branch_amount"] = branch_comparison[0]["net_sales"]
	else:
		kpis["top_branch"] = None
		kpis["top_branch_amount"] = 0

	trend_date_to = getdate(today())
	trend_date_from = add_days(trend_date_to, -(_TREND_DAYS - 1))
	trend = _get_trend_series(company, trend_date_from, trend_date_to, branch_filter)

	branches = _get_today_branch_profile_grid(company, branch_filter)
	open_shifts = _get_open_shifts(company, branch_filter)
	kpis["active_shift_count"] = len(open_shifts)
	payment_split = _get_payment_split(company, date_from, date_to, branch_filter=branch_filter)

	return {
		"company": company,
		"currency": currency,
		"date_from": str(date_from),
		"date_to": str(date_to),
		"trend_date_from": str(trend_date_from),
		"trend_date_to": str(trend_date_to),
		"trend_days": _TREND_DAYS,
		"kpis": kpis,
		"trend": trend,
		"branch_comparison": branch_comparison,
		"branches": branches,
		"active_shifts_preview": open_shifts[:5],
		"payment_split": payment_split,
	}


@frappe.whitelist()
def get_pos_profile_detail(pos_profile: str, date: str | None = None):
	"""Drill-through for one POS Profile card: hourly breakdown, payment-mode split,
	last 10 invoices, and a same-clock-time-yesterday comparison (a fair partial-day
	comparison when `date` is today, a plain full-day comparison otherwise)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	_check_dashboard_role()
	if not frappe.has_permission("POS Invoice", ptype="read"):
		frappe.throw(_("Not permitted to view sales data."), frappe.PermissionError)

	profile_meta = frappe.db.get_value(
		"POS Profile", pos_profile, ["name", "company", "branch"], as_dict=True
	)
	if not profile_meta:
		frappe.throw(_("POS Profile {0} was not found.").format(pos_profile))
	if profile_meta.branch and not frappe.has_permission("Branch", ptype="read", doc=profile_meta.branch):
		frappe.throw(_("Not permitted to view this branch."), frappe.PermissionError)

	currency = frappe.get_cached_value("Company", profile_meta.company, "default_currency")
	target_date = getdate(date) if date else getdate(today())
	start_time, end_time, is_today = _period_boundaries(target_date)
	today_total = _get_profile_period_total(pos_profile, start_time, end_time)

	yesterday_date = add_days(target_date, -1)
	y_start, y_end, _y_is_today = _period_boundaries(yesterday_date)
	if is_today:
		y_end = get_datetime(f"{yesterday_date} {now_datetime().strftime('%H:%M:%S')}")
	yesterday_total = _get_profile_period_total(pos_profile, y_start, y_end)

	return {
		"pos_profile": pos_profile,
		"branch": profile_meta.branch,
		"currency": currency,
		"date": str(target_date),
		"is_today": is_today,
		"today_total": today_total,
		"yesterday_total": yesterday_total,
		"hourly": _get_hourly_breakdown(pos_profile, target_date),
		"payment_split": _get_payment_split(
			profile_meta.company, target_date, target_date, branch_filter=None, pos_profile=pos_profile
		),
		"recent_invoices": _get_recent_invoices(pos_profile, limit=10),
	}


@frappe.whitelist()
def get_active_shifts_detail(company: str | None = None, branch: str | None = None):
	"""Full Active Shifts table for the KPI card's dialog - branch, profile, cashier,
	opened at, and each shift's live running total."""
	company, currency = _resolve_context(company, branch)
	branch_filter = _resolve_branch_filter(company, branch)
	return {"currency": currency, "active_shifts": _get_open_shifts(company, branch_filter)}


@frappe.whitelist()
def get_returns_detail(
	company: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	branch: str | None = None,
):
	company, currency = _resolve_context(company, branch)
	date_from, date_to = _get_date_range(date_from, date_to)
	branch_filter = _resolve_branch_filter(company, branch)
	if branch_filter is not None and not branch_filter:
		return {"currency": currency, "returns": []}

	rows = frappe.db.sql(
		f"""
        SELECT pi.name, pi.posting_date, pi.customer, pi.grand_total, pi.return_against,
               COALESCE(pi.branch, pp.branch) AS branch, pi.pos_profile
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1
          AND pi.company = %(company)s
          AND pi.is_return = 1
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {_branch_scope_clause(branch_filter)}
        ORDER BY pi.posting_date DESC, pi.name DESC
        """,
		{
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch_names": tuple(branch_filter) if branch_filter else (),
		},
		as_dict=True,
	)
	return {
		"currency": currency,
		"returns": [
			{
				"name": row.name,
				"posting_date": str(row.posting_date),
				"customer": row.customer,
				"amount": flt(-row.grand_total),
				"return_against": row.return_against,
				"branch": row.branch or _("Unassigned"),
				"pos_profile": row.pos_profile,
			}
			for row in rows
		],
	}


@frappe.whitelist()
def get_payment_mode_detail(
	company: str | None = None,
	date_from: str | None = None,
	date_to: str | None = None,
	branch: str | None = None,
):
	company, currency = _resolve_context(company, branch)
	date_from, date_to = _get_date_range(date_from, date_to)
	branch_filter = _resolve_branch_filter(company, branch)
	return {
		"currency": currency,
		"payment_split": _get_payment_split(company, date_from, date_to, branch_filter=branch_filter),
	}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_range_kpis(company: str, date_from, date_to, branch_filter: list[str] | None):
	if branch_filter is not None and not branch_filter:
		return {
			"net_sales": 0,
			"gross_sales": 0,
			"returns_amount": 0,
			"returns_count": 0,
			"txn_count": 0,
			"average_basket": 0,
		}

	rows = frappe.db.sql(
		f"""
        SELECT
            COALESCE(SUM(pi.grand_total), 0) AS net_sales,
            COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN pi.grand_total END), 0) AS gross_sales,
            COALESCE(SUM(CASE WHEN pi.is_return = 1 THEN -pi.grand_total END), 0) AS returns_amount,
            COALESCE(SUM(CASE WHEN pi.is_return = 1 THEN 1 ELSE 0 END), 0) AS returns_count,
            COALESCE(SUM(CASE WHEN pi.is_return = 0 THEN 1 ELSE 0 END), 0) AS sales_txn_count,
            COUNT(*) AS txn_count
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {_branch_scope_clause(branch_filter)}
        """,
		{
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch_names": tuple(branch_filter) if branch_filter else (),
		},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	sales_txn_count = cint(row.get("sales_txn_count"))
	gross_sales = flt(row.get("gross_sales"))
	return {
		"net_sales": flt(row.get("net_sales")),
		"gross_sales": gross_sales,
		"returns_amount": flt(row.get("returns_amount")),
		"returns_count": cint(row.get("returns_count")),
		"txn_count": cint(row.get("txn_count")),
		"average_basket": (gross_sales / sales_txn_count) if sales_txn_count else 0,
	}


def _get_trend_series(company: str, date_from, date_to, branch_filter: list[str] | None):
	by_date = {}
	if not (branch_filter is not None and not branch_filter):
		rows = frappe.db.sql(
			f"""
            SELECT pi.posting_date AS posting_date, COALESCE(SUM(pi.grand_total), 0) AS net_sales
            FROM `tabPOS Invoice` pi
            LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
            WHERE pi.docstatus = 1
              AND pi.company = %(company)s
              AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
              {_branch_scope_clause(branch_filter)}
            GROUP BY pi.posting_date
            """,
			{
				"company": company,
				"date_from": date_from,
				"date_to": date_to,
				"branch_names": tuple(branch_filter) if branch_filter else (),
			},
			as_dict=True,
		)
		by_date = {str(row.posting_date): flt(row.net_sales) for row in rows}

	series = []
	cursor = date_from
	while cursor <= date_to:
		series.append({"date": str(cursor), "net_sales": by_date.get(str(cursor), 0)})
		cursor = add_days(cursor, 1)
	return series


def _get_branch_comparison(company: str, date_from, date_to, branch_filter: list[str] | None):
	if branch_filter is not None and not branch_filter:
		return []

	rows = frappe.db.sql(
		f"""
        SELECT COALESCE(pi.branch, pp.branch) AS branch, COALESCE(SUM(pi.grand_total), 0) AS net_sales
        FROM `tabPOS Invoice` pi
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE pi.docstatus = 1
          AND pi.company = %(company)s
          AND pi.posting_date BETWEEN %(date_from)s AND %(date_to)s
          {_branch_scope_clause(branch_filter)}
        GROUP BY COALESCE(pi.branch, pp.branch)
        ORDER BY net_sales DESC
        """,
		{
			"company": company,
			"date_from": date_from,
			"date_to": date_to,
			"branch_names": tuple(branch_filter) if branch_filter else (),
		},
		as_dict=True,
	)
	return [{"branch": row.branch or _("Unassigned"), "net_sales": flt(row.net_sales)} for row in rows]


def _get_today_branch_profile_grid(company: str, branch_filter: list[str] | None):
	today_date = getdate(today())

	profiles = frappe.get_all(
		"POS Profile",
		filters={"company": company, "disabled": 0},
		fields=["name", "branch"],
		order_by="branch, name",
	)
	if branch_filter is not None:
		allowed = set(branch_filter)
		profiles = [p for p in profiles if p.branch in allowed]

	profile_names = [p.name for p in profiles]
	totals_by_profile = {}
	if profile_names:
		rows = frappe.db.sql(
			"""
            SELECT pos_profile,
                   COALESCE(SUM(grand_total), 0) AS today_sales,
                   COUNT(*) AS txn_count,
                   MAX(TIMESTAMP(posting_date, posting_time)) AS last_sale_at
            FROM `tabPOS Invoice`
            WHERE docstatus = 1
              AND posting_date = %(today)s
              AND pos_profile IN %(profile_names)s
            GROUP BY pos_profile
            """,
			{"today": today_date, "profile_names": tuple(profile_names)},
			as_dict=True,
		)
		totals_by_profile = {row.pos_profile: row for row in rows}

	open_shifts = _get_open_shifts(company, branch_filter)
	shift_by_profile = {}
	for shift in open_shifts:
		# openings are already ordered most-recent-first; the first one seen per
		# profile wins if more than one shift is (unusually) open on it at once.
		shift_by_profile.setdefault(shift["pos_profile"], shift)

	branch_names_from_profiles = sorted({p.branch for p in profiles if p.branch})
	# Unassigned is a synthetic bucket, not a real Branch a permission check can
	# gate - only ever shown when nothing has already narrowed the scope to
	# specific branches (an explicit filter or a restricted user's permission set).
	include_unassigned = branch_filter is None

	sections = [
		_build_branch_section(name, profiles, totals_by_profile, shift_by_profile)
		for name in branch_names_from_profiles
	]
	if include_unassigned:
		unassigned = _build_branch_section(None, profiles, totals_by_profile, shift_by_profile)
		if unassigned["profiles"]:
			sections.append(unassigned)
	return sections


def _build_branch_section(branch_name: str | None, profiles, totals_by_profile, shift_by_profile):
	section_profiles = []
	for profile in profiles:
		if (profile.branch or None) != branch_name:
			continue
		totals = totals_by_profile.get(profile.name)
		section_profiles.append(
			{
				"pos_profile": profile.name,
				"today_sales": flt(totals.today_sales) if totals else 0,
				"txn_count": cint(totals.txn_count) if totals else 0,
				"last_sale_at": str(totals.last_sale_at) if totals and totals.last_sale_at else None,
				"shift": shift_by_profile.get(profile.name),
			}
		)
	section_profiles.sort(key=lambda row: row["pos_profile"])
	return {
		"branch": branch_name or _("Unassigned"),
		"today_total": sum(row["today_sales"] for row in section_profiles),
		"profiles": section_profiles,
	}


def _get_open_shifts(company: str, branch_filter: list[str] | None):
	if branch_filter is not None and not branch_filter:
		return []

	openings = frappe.get_all(
		"POS Opening Entry",
		filters={"company": company, "docstatus": 1, "status": "Open"},
		fields=["name", "pos_profile", "user", "period_start_date"],
		order_by="period_start_date desc",
	)
	if not openings:
		return []

	profile_names = list({o.pos_profile for o in openings})
	profile_branch = {
		p.name: p.branch
		for p in frappe.get_all(
			"POS Profile", filters={"name": ["in", profile_names]}, fields=["name", "branch"]
		)
	}

	if branch_filter is not None:
		allowed = set(branch_filter)
		openings = [o for o in openings if profile_branch.get(o.pos_profile) in allowed]
		if not openings:
			return []

	user_names = list({o.user for o in openings})
	full_names = {
		u.name: u.full_name
		for u in frappe.get_all("User", filters={"name": ["in", user_names]}, fields=["name", "full_name"])
	}

	shifts = []
	for opening in openings:
		live = _compute_live_shift_total(opening.pos_profile, opening.user, opening.period_start_date)
		shifts.append(
			{
				"opening_entry": opening.name,
				"pos_profile": opening.pos_profile,
				"branch": profile_branch.get(opening.pos_profile) or _("Unassigned"),
				"user": opening.user,
				"cashier_full_name": full_names.get(opening.user) or opening.user,
				"opened_at": str(opening.period_start_date),
				"running_total": live["running_total"],
				"txn_count": live["txn_count"],
			}
		)
	return shifts


def _compute_live_shift_total(pos_profile: str, user: str, period_start_date) -> dict:
	"""Mirrors ERPNext's own live shift-total technique
	(pos_closing_entry.build_invoice_query: owner == user, docstatus == 1, is_pos == 1,
	pos_profile == pos_profile, timestamp between shift-open and now) so a shift that
	hasn't been closed yet still contributes an accurate running total here."""
	rows = frappe.db.sql(
		"""
        SELECT COALESCE(SUM(grand_total), 0) AS running_total, COUNT(*) AS txn_count
        FROM `tabPOS Invoice`
        WHERE docstatus = 1
          AND is_pos = 1
          AND owner = %(user)s
          AND pos_profile = %(pos_profile)s
          AND TIMESTAMP(posting_date, posting_time) BETWEEN %(start)s AND %(now)s
        """,
		{"user": user, "pos_profile": pos_profile, "start": period_start_date, "now": now_datetime()},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	return {"running_total": flt(row.get("running_total")), "txn_count": cint(row.get("txn_count"))}


def _period_boundaries(target_date):
	is_today = target_date == getdate(today())
	end_time = now_datetime() if is_today else get_datetime(f"{target_date} 23:59:59")
	start_time = get_datetime(f"{target_date} 00:00:00")
	return start_time, end_time, is_today


def _get_profile_period_total(pos_profile: str, start_time, end_time):
	rows = frappe.db.sql(
		"""
        SELECT COALESCE(SUM(grand_total), 0) AS amount, COUNT(*) AS txn_count
        FROM `tabPOS Invoice`
        WHERE docstatus = 1
          AND pos_profile = %(pos_profile)s
          AND TIMESTAMP(posting_date, posting_time) BETWEEN %(start)s AND %(end)s
        """,
		{"pos_profile": pos_profile, "start": start_time, "end": end_time},
		as_dict=True,
	)
	row = rows[0] if rows else {}
	return {"amount": flt(row.get("amount")), "txn_count": cint(row.get("txn_count"))}


def _get_hourly_breakdown(pos_profile: str, target_date):
	rows = frappe.db.sql(
		"""
        SELECT HOUR(posting_time) AS hour, COALESCE(SUM(grand_total), 0) AS amount, COUNT(*) AS txn_count
        FROM `tabPOS Invoice`
        WHERE docstatus = 1
          AND pos_profile = %(pos_profile)s
          AND posting_date = %(date)s
        GROUP BY HOUR(posting_time)
        ORDER BY hour
        """,
		{"pos_profile": pos_profile, "date": target_date},
		as_dict=True,
	)
	return [
		{"hour": cint(row.hour), "amount": flt(row.amount), "txn_count": cint(row.txn_count)} for row in rows
	]


def _get_payment_split(
	company: str, date_from, date_to, branch_filter: list[str] | None = None, pos_profile: str | None = None
):
	if branch_filter is not None and not branch_filter:
		return []

	conditions = [
		"pi.docstatus = 1",
		"pi.company = %(company)s",
		"pi.posting_date BETWEEN %(date_from)s AND %(date_to)s",
	]
	params = {"company": company, "date_from": date_from, "date_to": date_to}
	if pos_profile:
		conditions.append("pi.pos_profile = %(pos_profile)s")
		params["pos_profile"] = pos_profile
	if branch_filter is not None:
		conditions.append("COALESCE(pi.branch, pp.branch) IN %(branch_names)s")
		params["branch_names"] = tuple(branch_filter)

	where_clause = " AND ".join(conditions)
	rows = frappe.db.sql(
		f"""
        SELECT pay.mode_of_payment AS mode_of_payment, COALESCE(SUM(pay.amount), 0) AS amount
        FROM `tabSales Invoice Payment` pay
        INNER JOIN `tabPOS Invoice` pi ON pi.name = pay.parent AND pay.parenttype = 'POS Invoice'
        LEFT JOIN `tabPOS Profile` pp ON pp.name = pi.pos_profile
        WHERE {where_clause}
        GROUP BY pay.mode_of_payment
        ORDER BY amount DESC
        """,
		params,
		as_dict=True,
	)
	return [{"mode_of_payment": row.mode_of_payment, "amount": flt(row.amount)} for row in rows]


def _get_recent_invoices(pos_profile: str, limit: int = 10):
	rows = frappe.db.sql(
		"""
        SELECT name, posting_date, posting_time, customer, grand_total, is_return
        FROM `tabPOS Invoice`
        WHERE docstatus = 1
          AND pos_profile = %(pos_profile)s
        ORDER BY posting_date DESC, posting_time DESC, name DESC
        LIMIT %(limit)s
        """,
		{"pos_profile": pos_profile, "limit": cint(limit)},
		as_dict=True,
	)
	return [
		{
			"name": row.name,
			"posting_date": str(row.posting_date),
			"posting_time": str(row.posting_time),
			"customer": row.customer,
			"grand_total": flt(row.grand_total),
			"is_return": cint(row.is_return),
		}
		for row in rows
	]
