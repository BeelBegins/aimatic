# Retail POS server API

This is a high-volume transaction boundary. Load the bench `offline-pos`, FBR,
loyalty, shelf-pricing and purchase skills as applicable.

Preserve cashier versus supervisor identity, protected RPC permissions,
idempotency, shift/session lifecycle, server-authoritative price/payment,
stock/GL timing, return/cancel semantics, FBR failure logging and audit logs.
Never replace protected RPCs with raw resource reads or trust client-asserted
identity, totals or authorization.

Use static/unit/local checks first. No live transaction or destructive suite
without the production gate.
