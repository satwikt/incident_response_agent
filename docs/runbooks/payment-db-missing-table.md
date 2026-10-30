# Runbook: Payment database query fails (missing table)

**Service:** checkout
**Error signature:** `DatabaseRuntimeError`, `relation 'payment_transactions_v2' does not exist`, `UndefinedTable`
**Severity:** high. Every checkout returns HTTP 500.

## Symptom
- 100% of `POST /checkout` requests fail with HTTP 500, typically right after a release or migration.
- The log shows the exact missing relation name.

## Likely causes
1. **Code deployed ahead of its migration.** The new release queries a table the migration has not created yet.
2. **Migration failed or was skipped** in this environment.
3. **Fault injection** (`payment_db_fail`). If the log says the fault is active, disable it.

## Remediation
1. Fault active: `disable_fault`.
2. Migration missing: `run_db_migration` or `create_missing_table` for the relation named in the error.
3. Release is the cause and the migration cannot run now: `rollback_release`.
4. Never `restart_worker`: a restart does not create a table.

## Verify
Error rate on `/checkout` returns to 0% and the `DatabaseRuntimeError` line stops.
