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