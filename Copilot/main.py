"""Main FastAPI application entry point serving API and frontend UI."""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.router import router
from agent.tools import initialize_rag
from agent.watcher import start_watcher, stop_watcher

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_rag()
    watcher_task = await start_watcher()
    yield
    await stop_watcher(watcher_task)

app = FastAPI(title="SRE Copilot (Google ADK)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "framework": "google-adk"}


@app.get("/")
async def ui_root():
    return FileResponse(
        os.path.join(FRONTEND_DIR, "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
