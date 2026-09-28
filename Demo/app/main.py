import logging
import os

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

# OpenTelemetry imports
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

from database import get_db
from models import TodoDB, TodoCreate, TodoUpdate, TodoResponse
from faults import RouteKey, FaultName, apply_faults, toggle_fault, get_faults

# ─── OpenTelemetry Setup ────────────────────────────────────────────────────────

OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
resource = Resource.create({"service.name": "todo-app", "service.instance.id": "instance-1"})

# Tracing
tracer_provider = TracerProvider(resource=resource)
trace.set_tracer_provider(tracer_provider)
span_exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

# Metrics
metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=OTEL_ENDPOINT, insecure=True))
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("todo.meter")
faults_active = meter.create_up_down_counter("todo_faults_active", description="Number of active faults")

# Logging
LoggingInstrumentor().instrument(set_logging_format=True)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("todo.app")

# ─── FastAPI App ────────────────────────────────────────────────────────────────

app = FastAPI(title="SRE Copilot Todo App")
FastAPIInstrumentor.instrument_app(app)

# Serve Frontend
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": str(exc)})

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
async def update_todo(todo_id: int, todo: TodoUpdate, db: Session = Depends(get_db)):
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
async def delete_todo(todo_id: int, db: Session = Depends(get_db)):
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

# ─── Fault Control Endpoints ────────────────────────────────────────────────────

@app.get("/admin/faults")
def list_faults():
    """Return the current fault state for every route.

    Example response::

        {
            "get_todos":   {"latency_spike": false, "random_500_storm": false, ...},
            "post_todos":  {"latency_spike": true,  "random_500_storm": false, ...},
            ...
        }
    """
    return get_faults()


@app.post("/admin/faults/{route_key}/{fault_name}")
def set_fault(route_key: RouteKey, fault_name: FaultName, enabled: bool):
    """Enable or disable a fault on a specific route.

    Both ``route_key`` and ``fault_name`` are enums — use the Swagger dropdowns
    to select valid values.

    Example — inject latency on GET /todos::

        POST /admin/faults/get_todos/latency_spike?enabled=true

    Example — trigger random 500s on POST /todos::

        POST /admin/faults/post_todos/random_500_storm?enabled=true

    Example — simulate connection exhaustion on DELETE /todos/{todo_id}::

        POST /admin/faults/delete_todo/db_connection_leak?enabled=true
    """
    toggle_fault(route_key, fault_name, enabled)
    faults_active.add(1 if enabled else -1)
    return {"route": route_key.value, "fault": fault_name.value, "enabled": enabled}


@app.get("/health")
def get_health():
    """Health check endpoint. Always returns 200 regardless of active faults."""
    return {"status": "ok"}


if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
