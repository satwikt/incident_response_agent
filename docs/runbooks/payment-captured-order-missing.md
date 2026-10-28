# Runbook: Payment captured but order not created (RECONCILIATION_GAP)

**Service:** checkout (payment gateway -> order service)
**Error signature:** `CRITICAL RECONCILIATION_GAP`, `OrderServiceTimeout`, `order not persisted`, `gateway webhook retry pending`
**Severity:** critical. Customers are charged and no order exists.

## Symptom
- Checkout returns HTTP 200 (the response status is `PROCESSING`, not `CONFIRMED`), so the error rate and latency look normal.
- `GET /orders/{id}` returns 404 for orders customers were just shown.
- The reconciliation check logs `payments_captured` greater than `orders_persisted`.
- Support tickets follow within hours: "I was charged but have no confirmation".

## Why error-rate alerts miss it
The payment gateway call succeeds, so no 5xx is raised. The failure happens after the capture, when the order write to the
order service times out. Only comparing captured payments with persisted orders shows it.

## Likely causes (check in this order)
1. **Order service write timeout or saturation.** Look for `OrderServiceTimeout` in checkout logs and a slow or failing `checkout.persist_order` span.
2. **Order database unavailable or a migration in progress.** Persist errors that start right after a deploy point here.
3. **Idempotency or webhook handling bug.** The gateway retries the webhook but the order is rejected as a duplicate, so it is never created.
4. **Feature flag or fault injection left on** (`order_persist_fail`). A log line saying the fault is active means disable it first.

## Diagnosis steps
1. Read the `RECONCILIATION_GAP` log line: note `unmatched_amount` and the first payment ids.
2. Search logs for `OrderServiceTimeout` and `not persisted` to confirm where the write fails.
3. Open a trace for one affected checkout: `checkout.payment_gateway` is OK, `checkout.persist_order` has `order.persisted=false`.
4. Confirm the gap grows only while the cause is active (it stops growing once fixed).

## Remediation
1. **Stop the bleeding:** if a fault or flag is active, propose `disable_fault`. Otherwise fix the order service or database, and roll back the release if it started after a deploy (`rollback_release`).
2. **Repair the data:** run `reconcile_orders` (POST `/admin/reconcile` on checkout). It creates the missing order for every captured payment. If an order cannot be created, refund the payment through the gateway.
3. **Verify:** unmatched payments is 0 in checkout `/health`, and the `RECONCILIATION_GAP` line stops appearing.

## Do not
- Do not restart the worker: in-flight payments would be lost.
- Do not ask customers to retry the purchase before reconciling, or they will be charged twice.

**Proposed action for this incident type:** `disable_fault` while a fault is active, then `reconcile_orders`.
