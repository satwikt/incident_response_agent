"""Main FastAPI application entry point serving the API and the frontend UI."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.ingest import router as ingest_router
from api.router import router
from agent.watcher import start_watcher, stop_watcher

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher_task = await start_watcher()
    yield
    await stop_watcher(watcher_task)


# No interactive docs and no CORS: the UI is served from this same origin, and the only cross-origin
# caller (the monitored app) is a server, not a browser.
app = FastAPI(title="Incident Response Copilot", lifespan=lifespan, docs_url=None, redoc_url=None,
              openapi_url=None, redirect_slashes=False)

app.include_router(ingest_router)
app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def ui_root():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
