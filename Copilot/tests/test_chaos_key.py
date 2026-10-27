"""Fault-injection endpoints require X-Chaos-Key when CHAOS_KEY is set (lead L07), and stay open when it is not."""

import importlib.util
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

AUTH = Path(__file__).resolve().parents[2] / "services" / "common" / "auth.py"
spec = importlib.util.spec_from_file_location("chaos_auth", AUTH)
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/faults")
    def read():
        return {"ok": True}

    @app.post("/faults", dependencies=[Depends(auth.require_chaos_key)])
    def write():
        return {"changed": True}

    return TestClient(app)


def test_open_when_no_key_is_configured(client, monkeypatch):
    monkeypatch.delenv("CHAOS_KEY", raising=False)
    assert client.post("/faults").status_code == 200


def test_key_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("CHAOS_KEY", "demo-key-123")
    assert client.post("/faults").status_code == 401
    assert client.post("/faults", headers={"X-Chaos-Key": "wrong"}).status_code == 401
    assert client.post("/faults", headers={"X-Chaos-Key": "demo-key-123"}).status_code == 200


def test_reading_state_is_never_guarded(client, monkeypatch):
    monkeypatch.setenv("CHAOS_KEY", "demo-key-123")
    assert client.get("/faults").status_code == 200


def test_blank_or_whitespace_key_counts_as_not_configured(client, monkeypatch):
    monkeypatch.setenv("CHAOS_KEY", "   ")
    assert client.post("/faults").status_code == 200


def test_every_shop_service_guards_its_mutating_fault_routes():
    root = Path(__file__).resolve().parents[2] / "services"
    for svc, routes in (("checkout", ("/admin/faults", "/admin/faults/reset")), ("catalog", ("/admin/faults", "/admin/faults/reset")),
                        ("frontend", ("/api/faults", "/api/faults/reset"))):
        src = (root / svc / "main.py").read_text(encoding="utf-8")
        for r in routes:
            assert f'@app.post("{r}", dependencies=[Depends(require_chaos_key)])' in src, (svc, r)


def test_frontend_forwards_the_key_to_the_services_it_syncs():
    src = (Path(__file__).resolve().parents[2] / "services" / "frontend" / "main.py").read_text(encoding="utf-8")
    assert src.count("headers=_chaos_headers()") == 2
