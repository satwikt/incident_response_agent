import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from agent import config
from api import ingest
from db import events as store
from tests.conftest import KEY_A, KEY_B, count, ev, headers, make_app, rows


def post(client, body, key=KEY_A, **kw):
    return client.post("/ingest/events", json=body, headers=headers(key), **kw)


# ── happy path and namespacing ────────────────────────────────────────────────────────────

def test_valid_batch_is_stored_and_bound_to_the_keys_service(client):
    r = post(client, [ev("a"), ev("b", status=500, level="ERROR", exception="ValueError: x")])
    assert r.status_code == 200 and r.json() == {"accepted": 2, "duplicates": 0}
    got = rows()
    assert {g["service"] for g in got} == {"todo-app"}
    assert {g["id"] for g in got} == {"todo-app:a", "todo-app:b"}
    assert all(g["received_at"] >= g["ts"] - 5 * 60_000 for g in got)


def test_same_event_id_from_two_services_does_not_collide(client):
    assert post(client, [ev("same")], KEY_A).json()["accepted"] == 1
    assert post(client, [ev("same")], KEY_B).json()["accepted"] == 1
    assert count("todo-app") == 1 and count("other-app") == 1


def test_a_service_cannot_suppress_anothers_events_by_preclaiming_ids(client):
    post(client, [ev("victim-1")], KEY_B)  # attacker registers the id under ITS OWN service
    assert post(client, [ev("victim-1")], KEY_A).json()["accepted"] == 1


# ── authentication ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [None, "", "wrong", "key-for-todo-app ", "key-for-todo-ap", b"\xe9" * 40])
def test_bad_or_missing_key_is_401_with_identical_body(client, key):
    h = {} if key is None else {"X-Api-Key": key}
    r = client.post("/ingest/events", json=[ev()], headers=h)
    assert r.status_code == 401 and r.json() == {"detail": "unauthorized"}
    assert count() == 0


def test_no_configured_keys_fails_closed(client, monkeypatch):
    monkeypatch.setattr(config, "INGEST_KEYS", {})
    r = post(client, [ev()])
    assert r.status_code == 503 and count() == 0


def test_unauthenticated_caller_gets_no_body_processing(client):
    # Even an invalid body must produce 401 (not 422/413): nothing is parsed before auth.
    r = client.post("/ingest/events", content=b"\xff\xfe not json", headers={"X-Api-Key": "nope"})
    assert r.status_code == 401


# ── service binding ─────────────────────────────────────────────────────────────────────

def test_event_naming_another_service_is_rejected_and_nothing_is_stored(client):
    r = post(client, [ev("ok"), ev("forged", service="other-app")], KEY_A)
    assert r.status_code == 403 and r.json() == {"detail": "service mismatch"}
    assert count() == 0


def test_event_naming_its_own_service_is_fine(client):
    assert post(client, [ev("x", service="todo-app")]).status_code == 200


# ── size limits ─────────────────────────────────────────────────────────────────────────

def test_body_over_limit_is_413_via_content_length(client):
    big = b"[" + b"0," * 700_000 + b"0]"
    r = client.post("/ingest/events", content=big, headers=headers())
    assert r.status_code == 413 and count() == 0


def test_body_over_limit_is_413_when_streamed_without_content_length(client):
    def chunks():
        for _ in range(300):
            yield b"x" * 8192  # ~2.4 MB, unknown length up front
    r = client.post("/ingest/events", content=chunks(), headers=headers())
    assert r.status_code == 413 and count() == 0


def test_more_events_than_the_batch_limit_is_413(client):
    r = post(client, [ev(f"e{i}") for i in range(config.INGEST_MAX_BATCH + 1)])
    assert r.status_code == 413 and count() == 0


def test_exactly_the_batch_limit_is_accepted(client):
    r = post(client, [ev(f"e{i}") for i in range(config.INGEST_MAX_BATCH)])
    assert r.status_code == 200 and r.json()["accepted"] == config.INGEST_MAX_BATCH


def test_long_message_is_truncated_not_rejected(client):
    r = post(client, [ev("long", message="A" * 500_000, exception="B" * 500_000)])
    assert r.status_code == 200
    row = rows()[0]
    assert len(row["message"]) == config.MAX_MESSAGE_CHARS and len(row["exception"]) == config.MAX_EXCEPTION_CHARS


# ── hostile and malformed input ─────────────────────────────────────────────────────────

def test_nul_and_control_characters_are_stripped(client):
    post(client, [ev("c", message="a\x00b\x01c\x1fd\x7fe\nf\tg", route="GET /x\x00")])
    row = rows()[0]
    assert row["message"] == "abcde\nf\tg" and row["route"] == "GET /x"


def test_invalid_utf8_body_is_422(client):
    r = client.post("/ingest/events", content=b'[{"id":"\xff\xfe"}]', headers=headers())
    assert r.status_code == 422 and count() == 0


@pytest.mark.parametrize("body", [
    "not json", "{}", '"string"', "null", "123", '{"events": []}',
])
def test_non_array_or_unparseable_body_is_422(client, body):
    r = client.post("/ingest/events", content=body.encode(), headers=headers())
    assert r.status_code == 422


def test_deeply_nested_json_does_not_crash(client):
    r = client.post("/ingest/events", content=b"[" * 100_000 + b"]" * 100_000, headers=headers())
    assert r.status_code in (413, 422)


@pytest.mark.parametrize("bad", [
    {"route": "R" * 10_000},
    {"ts": 4_102_444_800_000},           # year 2100
    {"ts": 1_000_000},                    # 1970
    {"ts": "not a date"},
    {"ts": float("nan")},
    {"duration_ms": -1},
    {"duration_ms": float("inf")},
    {"duration_ms": float("nan")},
    {"duration_ms": 10**9},
    {"status": 99},
    {"status": 600},
    {"level": "FATAL"},
    {"id": ""},
    {"id": "x" * 65},
    {"id": "bad id!"},
    {"id": "../../etc/passwd"},
])
def test_invalid_field_is_422_and_the_whole_batch_is_rejected(client, bad):
    good = ev("good")
    r = client.post("/ingest/events", content=json.dumps([good, {**ev("bad"), **bad}]).encode(),
                    headers={**headers(), "Content-Type": "application/json"})
    assert r.status_code == 422
    assert count() == 0  # atomic: the valid neighbour was not stored either


def test_missing_required_fields_is_422(client):
    assert post(client, [{"id": "only-id"}]).status_code == 422


def test_error_bodies_never_echo_attacker_input(client):
    marker = "ATTACKER_MARKER_<script>alert(1)</script>"
    r = post(client, [ev("m", level=marker)])
    assert r.status_code == 422 and "ATTACKER_MARKER" not in r.text and "<script>" not in r.text


def test_level_is_normalised_to_upper_case(client):
    post(client, [ev("l", level="error")])
    assert rows()[0]["level"] == "ERROR"


def test_seconds_and_millisecond_and_iso_timestamps_are_accepted(client):
    from datetime import datetime, timezone

    now = store.now_ms()
    iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    r = post(client, [ev("s", ts=now / 1000), ev("m", ts=now), ev("i", ts=iso)])
    assert r.status_code == 200 and count() == 3
    assert all(abs(row["ts"] - now) < 60_000 for row in rows())  # all three normalised to epoch ms


def test_sql_injection_in_route_is_stored_literally(client):
    payload = "GET /todos'; DROP TABLE events;--"
    assert post(client, [ev("sqli", route=payload, message=payload)]).status_code == 200
    assert rows()[0]["route"] == payload  # and the table is still there (rows() queried it)


# ── idempotency and concurrency ─────────────────────────────────────────────────────────

def test_resending_a_batch_reports_duplicates_and_keeps_row_count(client):
    batch = [ev("d1"), ev("d2")]
    assert post(client, batch).json() == {"accepted": 2, "duplicates": 0}
    assert post(client, batch).json() == {"accepted": 0, "duplicates": 2}
    assert count() == 2


def test_ten_parallel_identical_batches_store_each_event_once():
    batch = [ev(f"p{i}") for i in range(50)]
    app = make_app()

    def send(_):
        return TestClient(app).post("/ingest/events", json=batch, headers=headers())

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(send, range(10)))
    assert all(r.status_code == 200 for r in results)
    assert sum(r.json()["accepted"] for r in results) == 50
    assert count() == 50


# ── rate limiting ───────────────────────────────────────────────────────────────────────

def test_rate_limit_returns_429_with_retry_after_and_is_per_key(client, monkeypatch):
    monkeypatch.setattr(config, "INGEST_RATE_BURST", 3)
    monkeypatch.setattr(config, "INGEST_RATE_PER_SEC", 0.0001)
    codes = [post(client, [ev(f"r{i}")]).status_code for i in range(6)]
    assert codes[:3] == [200, 200, 200] and set(codes[3:]) == {429}
    last = post(client, [ev("rx")])
    assert last.headers.get("retry-after") == "1"
    assert post(client, [ev("other")], KEY_B).status_code == 200  # other service unaffected


def test_rate_limited_requests_do_not_read_or_store_anything(client, monkeypatch):
    monkeypatch.setattr(config, "INGEST_RATE_BURST", 1)
    monkeypatch.setattr(config, "INGEST_RATE_PER_SEC", 0.0001)
    post(client, [ev("first")])
    before = count()
    assert post(client, [ev("second")]).status_code == 429
    assert count() == before


# ── retention and row cap ───────────────────────────────────────────────────────────────

def test_row_cap_keeps_only_the_newest(client, monkeypatch):
    post(client, [ev(f"c{i}") for i in range(30)])
    monkeypatch.setattr(store, "EVENTS_MAX_ROWS", 10)
    store.purge()
    left = rows()
    assert len(left) == 10 and left[-1]["id"] == "todo-app:c29"


def test_events_past_retention_are_purged(client, monkeypatch):
    now = store.now_ms()
    store.insert_events("todo-app", [{"id": "old", "ts": now, "level": "INFO"}], received_at=now - 30 * 3_600_000)
    store.insert_events("todo-app", [{"id": "new", "ts": now, "level": "INFO"}], received_at=now)
    store.purge(now)
    assert [r["id"] for r in rows()] == ["todo-app:new"]


# ── regressions from the M1 tester round ────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["message", "exception"])
@pytest.mark.parametrize("lone", ["\ud800", "\udc00", "\ud83d", "a\ud800b"])
def test_lone_surrogates_are_replaced_not_a_server_error(client, field, lone):
    # json.dumps writes these as \udXXX escapes, exactly what the tester sent.
    r = client.post("/ingest/events", content=json.dumps([ev("sur", **{field: lone})]).encode(), headers=headers())
    assert r.status_code == 200, r.text
    stored = rows()[0][field]
    assert "\ufffd" in stored and not any("\ud800" <= ch <= "\udfff" for ch in stored)


@pytest.mark.parametrize("field", ["route", "id", "level", "service"])
def test_lone_surrogates_in_any_other_field_are_never_a_5xx(client, field):
    r = client.post("/ingest/events", content=json.dumps([ev("ok", **{field: "\ud800"})]).encode(), headers=headers())
    assert r.status_code < 500 and count() in (0, 1)


def test_valid_surrogate_pairs_and_astral_characters_are_kept(client):
    body = b'[{"id":"emo","ts":%d,"level":"INFO","message":"ok \ud83d\ude00 \xf0\x9f\x98\x80"}]' % store.now_ms()
    assert client.post("/ingest/events", content=body, headers=headers()).status_code == 200
    assert rows()[0]["message"] == "ok \U0001F600 \U0001F600"


def test_encoding_error_in_the_store_is_a_422_not_a_500(client, monkeypatch):
    def boom(*a, **k):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed")
    monkeypatch.setattr(store, "insert_events", boom)
    assert post(client, [ev("u")]).status_code == 422


@pytest.mark.parametrize("field,value", [("message", {"a": 1}), ("message", 123), ("exception", ["x"]), ("exception", True)])
def test_non_string_message_or_exception_is_rejected_not_silently_dropped(client, field, value):
    assert post(client, [ev("t", **{field: value})]).status_code == 422 and count() == 0


def test_null_message_and_exception_are_fine(client):
    assert post(client, [ev("n", message=None, exception=None)]).status_code == 200


def test_body_size_boundary_is_exact(client, monkeypatch):
    body = json.dumps([ev("b")]).encode()
    monkeypatch.setattr(config, "INGEST_MAX_BODY_BYTES", len(body))
    assert client.post("/ingest/events", content=body, headers=headers()).status_code == 200
    monkeypatch.setattr(config, "INGEST_MAX_BODY_BYTES", len(body) - 1)
    assert client.post("/ingest/events", content=body, headers=headers()).status_code == 413


def test_route_length_boundary(client):
    assert post(client, [ev("r200", route="R" * 200)]).status_code == 200
    assert post(client, [ev("r201", route="R" * 201)]).status_code == 422


def test_exception_is_truncated_to_the_documented_limit(client):
    post(client, [ev("x", exception="E" * 20_000)])
    assert len(rows()[0]["exception"]) == config.MAX_EXCEPTION_CHARS == 8192


def test_no_trailing_slash_redirects_on_the_ingest_route(client):
    r = client.post("/ingest/events/", json=[ev("s")], headers={**headers(), "Host": "evil.example"}, follow_redirects=False)
    assert r.status_code in (404, 405) and "location" not in r.headers
