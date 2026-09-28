# Fault Injection

The Demo app includes a built-in fault-injection framework that lets you simulate real-world failure scenarios on a live, running application — without any code changes or restarts.

Faults are controlled via the `/admin/faults` API and take effect **immediately** on subsequent requests.

---

## Fault Flags

| Flag                 | Default | Description |
|----------------------|---------|-------------|
| `latency_spike`      | `false` | Adds a random 2–5 second delay to every request |
| `memory_leak`        | `false` | Appends 1 MB to an in-memory list on every request |
| `db_connection_leak` | `false` | Prevents database sessions from being closed after each request |
| `random_500_storm`   | `false` | Randomly raises an unhandled `ValueError` on ~30% of requests, resulting in HTTP 500 |

---

## Fault Details

### `latency_spike`

Simulates high-latency conditions such as slow database queries or downstream service delays.

- **Effect:** Every qualifying request sleeps for a random duration between **2 and 5 seconds** before the route handler is invoked.
- **Scope:** All non-admin requests.

```bash
# Enable
curl -X POST "http://localhost:8000/admin/faults/latency_spike?enabled=true"

# Disable
curl -X POST "http://localhost:8000/admin/faults/latency_spike?enabled=false"
```

---

### `memory_leak`

Simulates a gradual memory leak accumulating over request volume.

- **Effect:** 1 MB is appended to a global list (`leaked_memory`) on every qualifying request. Memory grows unbounded until the fault is disabled.
- **Recovery:** Disabling this fault automatically clears the `leaked_memory` list, freeing the accumulated memory.
- **Scope:** All non-admin requests.

```bash
# Enable
curl -X POST "http://localhost:8000/admin/faults/memory_leak?enabled=true"

# Disable (also clears leaked memory)
curl -X POST "http://localhost:8000/admin/faults/memory_leak?enabled=false"
```

---

### `db_connection_leak`

Simulates connection pool exhaustion by preventing sessions from being returned after each request.

- **Effect:** The `finally` block in `get_db()` skips `db.close()`, leaving sessions open and consuming connection pool slots.
- **Recovery:** Disable the flag and restart the container to fully reset the connection pool.
- **Scope:** All database-backed requests (`/todos` routes).

```bash
# Enable
curl -X POST "http://localhost:8000/admin/faults/db_connection_leak?enabled=true"

# Disable
curl -X POST "http://localhost:8000/admin/faults/db_connection_leak?enabled=false"
```

---

### `random_500_storm`

Simulates an unstable application state where a significant portion of requests fail unpredictably.

- **Effect:** Each qualifying request has a **30% probability** of raising an unhandled `ValueError`, which the global exception handler converts to an `HTTP 500` response.
- **Scope:** All non-admin requests.

```bash
# Enable
curl -X POST "http://localhost:8000/admin/faults/random_500_storm?enabled=true"

# Disable
curl -X POST "http://localhost:8000/admin/faults/random_500_storm?enabled=false"
```

---

## Checking Active Faults

```bash
curl http://localhost:8000/admin/faults
```

Example response when `latency_spike` is active:

```json
{
  "latency_spike":      true,
  "memory_leak":        false,
  "db_connection_leak": false,
  "random_500_storm":   false
}
```

---

## Notes

- **Admin bypass:** The `/admin/*` endpoints and the root `/` route always bypass all fault middleware, ensuring you can always inspect and toggle fault state even during a `random_500_storm`.
- **Fault stacking:** Multiple faults can be active simultaneously. Effects are applied in the following order: `random_500_storm` → `memory_leak` → `latency_spike`.
- **Persistence:** Fault state is held in-memory and resets to all-`false` on container restart.
