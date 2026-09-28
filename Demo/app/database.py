import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base

DB_PATH = os.getenv("DB_PATH", "sqlite:///./todos.db")

# Create engine
engine = create_engine(DB_PATH, connect_args={"check_same_thread": False})

Base.metadata.create_all(bind=engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Provide a SQLAlchemy session for a request and always close it.

    Connection-pool exhaustion is simulated by ``faults.FaultState`` (a counter),
    not by leaking real sessions.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
