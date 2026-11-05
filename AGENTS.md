# Aimatic application adapter

Read and follow `/home/nabeel/frappe-bench/AGENTS.md` before work, including
in isolated worktrees. It owns shared rules, project routing, and delegation.
Select this app's relevant project and skill through its `docs/architecture.md`.
If the shared repository is unavailable, ask the lead for its provisioned path.
Do not duplicate runtime state or safety rules here.

For SZL Retail Operations changes (including Desk pages and payment/Discord hooks),
update `docs/szl-purchase-inventory-guide.md` to match the final behavior and regenerate
its companion PDF with the bench Python and `scripts/build_retail_operations_guide.py`.
Commit the guide and PDF with the behavior change; distinguish implemented behavior
from proposed improvements and preserve the published reorder formulas.
