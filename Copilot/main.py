"""Main FastAPI application entry point serving the API and the frontend UI."""

import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.ingest import router as ingest_router
from api.router import router
from api.dashboard import router as dashboard_router
from agent.watcher import start_watcher, stop_watcher

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


def reindex_runbooks() -> None:
    """Fold the mounted runbooks (docs/runbooks) into the docs index so search_docs cites them. Fails open."""
    runbooks = os.getenv("RUNBOOKS_PATH", "/app/docs/runbooks")
    if not os.path.isdir(runbooks):
        return
    try:
        subprocess.run([sys.executable, "scripts/index_docs.py", "--docs-path", "/nonexistent", "--extra-docs-path", runbooks,
                        "--merge", "--skip-chroma", "--index-path", os.getenv("DOCS_INDEX_PATH", "data/docs_index.json"),
                        "--chroma-path", "data/chroma_db"], check=True, timeout=120, capture_output=True)
    except Exception as exc:  # noqa: BLE001 - a missing runbook index must never stop the app
        logging.getLogger("copilot.main").warning("runbook re-index failed (%s)", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    reindex_runbooks()
    watcher_task = await start_watcher()
    yield
    await stop_watcher(watcher_task)


# No interactive docs and no CORS: the UI is served from this same origin, and the only cross-origin
# caller (the monitored app) is a server, not a browser.
app = FastAPI(title="Incident Response Copilot", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=None, redirect_slashes=False)

app.include_router(ingest_router)
app.include_router(router)
app.include_router(dashboard_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def ui_root():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/dashboard")
async def dashboard_ui():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "dashboard.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
