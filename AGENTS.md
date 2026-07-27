# Aimatic application guidance

Local code and uncommitted diffs are the immediate source of truth. Durable
architecture, migration, current-state and incident guidance is maintained in
`/home/nabeel/frappe-bench/CLAUDE.md` and its routed `.claude/skills/`.

## Safety

- Never modify Frappe, ERPNext or HRMS core for an aimatic feature.
- Inspect status/diff first and preserve unfamiliar local changes.
- Read-only site diagnosis is allowed. Live mutation/deploy/migrate/import
  requires explicit approval, current backup, verification and rollback.
- SZL was designated future production but not live as of 2026-07-28; verify.
- The POS handles roughly 2,000 daily transactions. Treat transaction,
  payment, pricing, offline/idempotency, stock/GL, FBR and permission paths as
  high risk.
- Never commit credentials. Public OAuth clients use PKCE without secrets.
- Safe static/local checks are allowed; no destructive/live suites without
  approval.

## Development

Own behavior in this app through modules, hooks, fixtures and ordered patches.
Keep patches idempotent. Validate JSON fixtures and Python/JS syntax. Trace
full document lifecycles including returns, cancellation, amendment and
retries. Commit coherent behaviors and update the owning skill/reference when
a durable fact changes.
