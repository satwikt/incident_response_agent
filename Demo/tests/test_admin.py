import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import admin
import faults

CHAOS, OPS = "chaos-secret-value", "ops-secret-value"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CHAOS_KEY", CHAOS)
    monkeypatch.setenv("OPS_KEY", OPS)
    faults.state.reset()
    app = FastAPI()
    app.include_router(admin.router)
    yield TestClient(app)
    faults.state.reset()


def h(key):
    return {"X-Api-Key": key}


ALL_ENDPOINTS = [
    ("GET", "/chaos", CHAOS),
    ("POST", "/chaos/reset", CHAOS),
    ("POST", "/chaos/bad_config/post_todos?enabled=true", CHAOS),
    ("GET", "/ops/state", OPS),
    ("POST", "/ops/flush_pool", OPS),
]


@pytest.mark.parametrize("method,path,_key", ALL_ENDPOINTS)
def test_missing_key_is_401(client, method, path, _key):
    assert client.request(method, path).status_code == 401


@pytest.mark.parametrize("method,path,_key", ALL_ENDPOINTS)
@pytest.mark.parametrize(
    "bad",
    ["", "x", "chaos-secret-valu", "chaos-secret-value ", "é".encode("latin-1") * 40],  # last: raw non-ASCII bytes
)
def test_wrong_key_is_401_with_identical_body(client, method, path, _key, bad):
    good_body = client.request(method, path).json()
    r = client.request(method, path, headers=h(bad))
    assert r.status_code == 401
    assert r.json() == good_body == {"detail": "unauthorized"}


@pytest.mark.parametrize("method,path,key", ALL_ENDPOINTS)
def test_correct_key_works(client, method, path, key):
    assert client.request(method, path, headers=h(key)).status_code == 200


def test_keys_are_not_interchangeable(client):
    assert client.post("/ops/flush_pool", headers=h(CHAOS)).status_code == 401
    assert client.post("/chaos/bad_config/post_todos?enabled=true", headers=h(OPS)).status_code == 401
    assert faults.state.chaos_snapshot() == {}


@pytest.mark.parametrize("name", ["CHAOS_KEY", "OPS_KEY"])
def test_unset_key_fails_closed_even_for_empty_header(client, monkeypatch, name):
    monkeypatch.delenv(name)
    path = "/chaos" if name == "CHAOS_KEY" else "/ops/state"
    for headers in ({}, h(""), h("anything")):
        r = client.get(path, headers=headers)
        assert r.status_code == 503
        assert name not in r.text  # does not leak which variable is missing


def test_empty_configured_key_fails_closed(client, monkeypatch):
    monkeypatch.setenv("OPS_KEY", "")
    assert client.get("/ops/state", headers=h("")).status_code == 503


def test_unknown_cause_action_route_are_422(client):
    assert client.post("/chaos/nope/post_todos?enabled=true", headers=h(CHAOS)).status_code == 422
    assert client.post("/chaos/bad_config/nope?enabled=true", headers=h(CHAOS)).status_code == 422
    assert client.post("/chaos/bad_config/post_todos", headers=h(CHAOS)).status_code == 422  # enabled required
    assert client.post("/ops/rm_rf", headers=h(OPS)).status_code == 422


def test_chaos_round_trip_and_reset(client):
    client.post("/chaos/bad_deploy/post_todos?enabled=true", headers=h(CHAOS))
    assert client.get("/chaos", headers=h(CHAOS)).json() == {"bad_deploy": ["post_todos"]}
    assert client.get("/ops/state", headers=h(OPS)).json()["release"] == faults.BAD_RELEASE
    client.post("/chaos/reset", headers=h(CHAOS))
    assert client.get("/chaos", headers=h(CHAOS)).json() == {}


def test_ops_action_fixes_and_response_does_not_reveal_effect(client):
    client.post("/chaos/bad_deploy/post_todos?enabled=true", headers=h(CHAOS))
    r = client.post("/ops/rollback_release", headers=h(OPS))
    assert r.json() == {"action": "rollback_release", "applied": True}
    assert client.get("/chaos", headers=h(CHAOS)).json() == {}


def test_ops_state_is_read_only_facts(client):
    body = client.get("/ops/state", headers=h(OPS)).json()
    assert set(body) == {
        "release", "config_rev", "pool_used", "pool_size", "leaked_mb", "fallback_enabled", "worker_uptime_s",
    }
