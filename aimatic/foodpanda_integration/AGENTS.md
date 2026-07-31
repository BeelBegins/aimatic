# Foodpanda Partner API integration

Load the bench `foodpanda-integration` skill. Keep credentials in
`Foodpanda Settings`/`Foodpanda Outlet` (Password fields only), never in code,
fixtures, logs, or error messages. Keep the order-webhook idempotency gate
(`Foodpanda Order Log.foodpanda_order_id`) and the catalog content-hash skip
as the two invariants that must not regress - both exist to stop duplicate
Sales Orders and redundant catalog pushes on webhook/job retries.

Never call the live or sandbox Foodpanda endpoint without explicit approval,
current credentials, and a rollback path. Several endpoint paths and enum
values here were taken from the published API spec but have not been
exercised against a real response yet - see the module docstrings in
`client.py`, `catalog.py`, `outlet.py`, and `orders.py` for exactly which
ones, and re-verify before the first real call.
