# Legacy iPOS and SZL migration

Every script and runbook here is durable migration source. Never delete an old
migration merely because it has run; it may be needed for audit, another site,
reconciliation or rollback analysis.

Before work, load the bench `ipos-migration` skill and `setup_szl.md`. Preserve
established mappings and deferred-scope decisions. Migration scripts must be
idempotent or clearly one-time, explicit about source/target, and must not
contain credentials. Read DB credentials from bench configuration.

Do not execute an import, setup phase, raw cross-database copy, opening entry,
stock/pricing load or site mutation without explicit approval, a current
backup, dry/read-only reconciliation, verification totals and rollback plan.
SZL was prepared but not live as of 2026-07-28; verify current state.
