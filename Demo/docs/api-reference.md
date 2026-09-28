# API Reference

**Base URL:** `http://localhost:8000`

---

## Todo Endpoints

### `GET /todos`

Returns all todos.

**Response `200 OK`**
```json
[
  { "id": 1, "title": "Buy groceries", "completed": false },
  { "id": 2, "title": "Write docs",    "completed": true  }
]
```

**curl**
```bash
curl http://localhost:8000/todos
```

---

### `POST /todos`

Creates a new todo.

**Request Body**
```json
{ "title": "Buy groceries", "completed": false }
```

| Field       | Type    | Required | Default |
|-------------|---------|----------|---------|
| `title`     | string  | ✅ Yes   | —       |
| `completed` | boolean | ❌ No    | `false` |

**Response `200 OK`**
```json
{ "id": 3, "title": "Buy groceries", "completed": false }
```

**curl**
```bash
curl -X POST http://localhost:8000/todos \
  -H "Content-Type: application/json" \
  -d '{"title": "Buy groceries"}'
```

---

### `PUT /todos/{todo_id}`

Updates an existing todo by ID. All fields are optional — only provided fields are updated.

**Path Parameters**

| Parameter | Type    | Description       |
|-----------|---------|-------------------|
| `todo_id` | integer | ID of the todo    |

**Request Body**
```json
{ "completed": true }
```

| Field       | Type    | Required |
|-------------|---------|----------|
| `title`     | string  | ❌ No   |
| `completed` | boolean | ❌ No   |

**Response `200 OK`**
```json
{ "id": 1, "title": "Buy groceries", "completed": true }
```

**Response `404 Not Found`**
```json
{ "detail": "Todo not found" }
```

**curl**
```bash
curl -X PUT http://localhost:8000/todos/1 \
  -H "Content-Type: application/json" \
  -d '{"completed": true}'
```

---

### `DELETE /todos/{todo_id}`

Deletes a todo by ID.

**Path Parameters**

| Parameter | Type    | Description    |
|-----------|---------|----------------|
| `todo_id` | integer | ID of the todo |

**Response `200 OK`**
```json
{ "detail": "Todo deleted" }
```

**Response `404 Not Found`**
```json
{ "detail": "Todo not found" }
```

**curl**
```bash
curl -X DELETE http://localhost:8000/todos/1
```

---

## Admin Endpoints

> Admin endpoints bypass all fault middleware and are always reachable.

---

### `GET /admin/faults`

Returns the current state of all fault flags.

**Response `200 OK`**
```json
{
  "latency_spike":      false,
  "memory_leak":        false,
  "db_connection_leak": false,
  "random_500_storm":   false
}
```

**curl**
```bash
curl http://localhost:8000/admin/faults
```

---

### `POST /admin/faults/{fault_name}`

Enables or disables a specific fault flag.

**Path Parameters**

| Parameter    | Type   | Description                              |
|--------------|--------|------------------------------------------|
| `fault_name` | string | One of: `latency_spike`, `memory_leak`, `db_connection_leak`, `random_500_storm` |

**Query Parameters**

| Parameter | Type    | Required | Description              |
|-----------|---------|----------|--------------------------|
| `enabled` | boolean | ✅ Yes   | `true` to enable, `false` to disable |

**Response `200 OK`**
```json
{ "fault": "latency_spike", "enabled": true }
```

**Response `404 Not Found`**
```json
{ "detail": "Fault flag not found" }
```

**curl — Enable a fault**
```bash
curl -X POST "http://localhost:8000/admin/faults/latency_spike?enabled=true"
```

**curl — Disable a fault**
```bash
curl -X POST "http://localhost:8000/admin/faults/latency_spike?enabled=false"
```
