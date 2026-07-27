# Shelf and Foodpanda pricing

Load the bench `shelf-pricing` and `purchase-cycle` skills. Keep validation,
price application and cancel-safe restoration as separate mechanisms. Route
Selling and Foodpanda prices by the correct branch-owned price list and use
the dedicated Foodpanda source field.

Never update live Item Price rows without explicit approval, backup/evidence,
pre/post reconciliation and rollback. Test submission, cancellation, return,
duplicate valid rows and retry behavior.
