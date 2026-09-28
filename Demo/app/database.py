import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from models import Base

DB_PATH = os.getenv("DB_PATH", "sqlite:///./todos.db")

# Create engine
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})

# Instrument the database engine
SQLAlchemyInstrumentor().instrument(engine=engine)

Base.metadata.create_all(bind=engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Provide a SQLAlchemy session for a request.

    Reads ``_current_route`` (set by ``apply_faults()``) to decide whether to
    honour the ``db_connection_leak`` fault for the active route.  If the fault
    is enabled the session is intentionally **not** closed, simulating
    connection-pool exhaustion.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        from faults import _route_faults, _current_route
        route = _current_route.get()
        if route and _route_faults.get(route, {}).get("db_connection_leak"):
            pass  # intentionally skip db.close() to simulate the leak
        else:
            db.close()
