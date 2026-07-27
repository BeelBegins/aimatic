# Branch management

Branch, warehouse, cost center, company, POS Profile and price-list context
must stay aligned. Do not introduce a generic fallback that can silently post
or price a transaction against the wrong store. Branch creation must preserve
its setup invariants and idempotent initialization.

Trace changes into stock, GL, permissions, mobile/POS context and reporting.
Use read-only reconciliation before repair and the production gate for live
setup or backfill.
