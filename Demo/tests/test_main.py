"""Tests against the real demo app (main.py): error bodies, id bounds, redirects."""

import os
import tempfile

os.environ["DB_PATH"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="demo-tests-"), "todos.db").replace("\\", "/")
os.environ.pop("COPILOT_INGEST_URL", None)
os.environ.pop("INGEST_KEY", None)

import pytest
from fastapi.testclient import TestClient

import main
from faults import PoolExhausted, state

client = TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset():
    state.reset()
    yield
    state.reset()


def test_crud_still_works():
    r = client.post("/todos", json={"title": "a", "completed": False})
    assert r.status_code == 200
    tid = r.json()["id"]
    assert client.put(f"/todos/{tid}", json={"completed": True}).json()["completed"] is True
    assert client.delete(f"/todos/{tid}").status_code == 200
    assert client.put(f"/todos/{tid}", json={"completed": True}).status_code == 404


@pytest.mark.parametrize("method", ["put", "delete"])
@pytest.mark.parametrize("bad_id", ["99999999999999999999", "0", "-1", "2147483648", "abc"])
def test_out_of_range_ids_are_422_not_a_500(method, bad_id):
    kw = {"json": {"title": "x"}} if method == "put" else {}
    r = getattr(client, method)(f"/todos/{bad_id}", **kw)
    assert r.status_code == 422
    assert "SQLite" not in r.text and "Python" not in r.text


def test_unexpected_errors_return_a_generic_body(monkeypatch):
    async def boom(route):
        raise RuntimeError("secret internal path /srv/app/db.py password=hunter2")
    monkeypatch.setattr(main, "apply_faults", boom)
    r = client.get("/todos")
    assert r.status_code == 500 and r.json() == {"detail": "Internal Server Error"}
    assert "hunter2" not in r.text and "/srv/app" not in r.text


def test_injected_faults_still_surface_their_message_and_status(monkeypatch):
    async def pool(route):
        raise PoolExhausted("connection pool exhausted")
    monkeypatch.setattr(main, "apply_faults", pool)
    r = client.get("/todos")
    assert r.status_code == 503 and r.json() == {"detail": "connection pool exhausted"}


def test_no_redirects_that_trust_the_host_header():
    for path in ("/todos/", "/chaos/", "/ops/state/"):
        r = client.get(path, headers={"Host": "evil.example"}, follow_redirects=False)
        assert r.status_code in (401, 404, 405, 503) and "location" not in r.headers, path


def test_docs_and_old_admin_paths_are_gone():
    for path in ("/docs", "/redoc", "/openapi.json", "/admin/faults"):
        assert client.get(path).status_code == 404
