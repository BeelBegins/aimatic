# FBR integration

FBR changes are compliance-critical. Load the bench `fbr-integration` skill.
Preserve scenario mapping, submission idempotency, response/error evidence,
retry behavior and diagnosable failure logging. Do not silently fix the
skill's flagged open issue as a side effect.

Never send test invoices to a live endpoint or mutate live fiscalization
settings without explicit approval, backup/config capture, verification and
rollback.
