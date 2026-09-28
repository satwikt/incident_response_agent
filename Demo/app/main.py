import logging
import os
from typing import Annotated

from fastapi import FastAPI, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from database import get_db
from models import TodoDB, TodoCreate, TodoUpdate, TodoResponse
from faults import RouteKey, FaultError, apply_faults, state as fault_state
from telemetry import EventEmitter, install as install_telemetry
from admin import router as admin_router

# Ids beyond SQLite's integer range would otherwise surface as an unexpected 500.
TodoId = Annotated[int, Path(ge=1, le=2**31 - 1)]

# ─── Logging and telemetry ──────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("todo.app")

SERVICE_NAME = os.getenv("SERVICE_NAME", "todo-app")
emitter = EventEmitter(
    url=os.getenv("COPILOT_INGEST_URL", ""),
    api_key=os.getenv("INGEST_KEY", ""),
    service=SERVICE_NAME,
)

# ─── FastAPI App ────────────────────────────────────────────────────────────────

app = FastAPI(title="Todo App", docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
app.include_router(admin_router)
install_telemetry(
    app,
    emitter,
    context_fn=fault_state.context_text,
    status_for_exc=lambda exc: exc.status_code if isinstance(exc, FaultError) else 500,
)


@app.on_event("startup")
def _start_emitter() -> None:
    emitter.start()


@app.on_event("shutdown")
def _stop_emitter() -> None:
    emitter.stop()

# Serve Frontend
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"Unhandled exception: {exc}", exc_info=True)
    if isinstance(exc, FaultError):
        # Injected incidents surface their message on purpose: it is the evidence the agent reads.
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})
    # Anything else is a real bug: never leak internals to the client.
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

# ─── Todo CRUD Endpoints ────────────────────────────────────────────────────────

@app.get("/todos", response_model=list[TodoResponse])
async def get_todos(db: Session = Depends(get_db)):
    """Return all todo items.

    Fault injection is applied before the DB call so that any induced latency
    or error is fully captured in the ``GET /todos`` OTel span.

    Example::

        GET /todos
    """
    await apply_faults(RouteKey.get_todos)
    log.info("Fetching all todos")
    return db.query(TodoDB).all()


@app.post("/todos", response_model=TodoResponse)
async def create_todo(todo: TodoCreate, db: Session = Depends(get_db)):
    """Create a new todo item.

    Example::

        POST /todos
        {"title": "Buy groceries", "completed": false}
    """
    await apply_faults(RouteKey.post_todos)
    log.info(f"Creating todo: {todo.title}")
    db_todo = TodoDB(title=todo.title, completed=todo.completed)
    db.add(db_todo)
    db.commit()
    db.refresh(db_todo)
    return db_todo


@app.put("/todos/{todo_id}", response_model=TodoResponse)
async def update_todo(todo_id: TodoId, todo: TodoUpdate, db: Session = Depends(get_db)):
    """Update an existing todo by ID. All fields are optional.

    Example::

        PUT /todos/1
        {"completed": true}
    """
    await apply_faults(RouteKey.put_todo)
    log.info(f"Updating todo {todo_id}")
    db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
    if not db_todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    if todo.title is not None:
        db_todo.title = todo.title
    if todo.completed is not None:
        db_todo.completed = todo.completed
    db.commit()
    db.refresh(db_todo)
    return db_todo


@app.delete("/todos/{todo_id}")
async def delete_todo(todo_id: TodoId, db: Session = Depends(get_db)):
    """Delete a todo by ID.

    Example::

        DELETE /todos/3
    """
    await apply_faults(RouteKey.delete_todo)
    log.info(f"Deleting todo {todo_id}")
    db_todo = db.query(TodoDB).filter(TodoDB.id == todo_id).first()
    if not db_todo:
        raise HTTPException(status_code=404, detail="Todo not found")
    db.delete(db_todo)
    db.commit()
    return {"detail": "Todo deleted"}

@app.get("/health")
def get_health():
    """Health check endpoint. Always returns 200 regardless of active faults."""
    return {"status": "ok"}


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
