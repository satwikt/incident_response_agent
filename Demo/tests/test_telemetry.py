import threading
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import telemetry
from faults import FaultError, PoolExhausted
from telemetry import EventEmitter, build_event, install


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# ── emitter ─────────────────────────────────────────────────────────────────────────────

def test_disabled_without_url_and_key_is_a_noop():
    e = EventEmitter(url="", api_key="")
    assert not e.enabled
    e.emit({"id": "1"})
    e.start()
    assert e._q.qsize() == 0 and e._thread is None


def test_events_are_batched_and_delivered():
    got = []
    e = EventEmitter(sender=got.append, batch_size=10, flush_interval_s=0.1)
    e.start()
    for i in range(25):
        e.emit({"id": str(i)})
    assert wait_for(lambda: sum(len(b) for b in got) == 25)
    e.stop()
    assert all(len(b) <= 10 for b in got) and e.sent == 25 and e.dropped == 0


def test_emit_never_blocks_even_if_the_sender_is_stuck():
    gate = threading.Event()
    e = EventEmitter(sender=lambda batch: gate.wait(5), max_queue=5, batch_size=1, flush_interval_s=0.05)
    e.start()
    start = time.perf_counter()
    for i in range(1000):
        e.emit({"id": str(i)})
    elapsed = time.perf_counter() - start
    gate.set()
    e.stop(1.0)
    assert elapsed < 0.5          # the request path never waited on the network
    assert e.dropped > 900        # overflow is dropped and counted, not queued forever


def test_sender_failures_are_swallowed_retried_and_counted(monkeypatch):
    calls = []

    def failing(batch):
        calls.append(len(batch))
        raise ConnectionError("copilot down")

    e = EventEmitter(sender=failing, max_attempts=3, batch_size=5, flush_interval_s=0.05)
    monkeypatch.setattr(e._stop, "wait", lambda t: False)  # no real backoff sleeping in the test
    e.start()
    for i in range(5):
        e.emit({"id": str(i)})
    assert wait_for(lambda: e.failed_batches == 1)
    e.stop()
    assert calls == [5, 5, 5] and e.dropped == 5 and e.sent == 0


def test_recovers_after_a_transient_failure(monkeypatch):
    state = {"n": 0}
    got = []

    def flaky(batch):
        state["n"] += 1
        if state["n"] == 1:
            raise TimeoutError("slow")
        got.append(batch)

    e = EventEmitter(sender=flaky, max_attempts=3, batch_size=3, flush_interval_s=0.05)
    monkeypatch.setattr(e._stop, "wait", lambda t: False)
    e.start()
    for i in range(3):
        e.emit({"id": str(i)})
    assert wait_for(lambda: e.sent == 3)
    e.stop()
    assert e.failed_batches == 0 and len(got) == 1


def test_http_sender_sends_json_with_the_api_key(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, key=req.get_header("X-api-key"), body=req.data, timeout=timeout,
                    method=req.get_method(), ctype=req.get_header("Content-type"))
        return Resp()

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake_urlopen)
    EventEmitter(url="http://copilot:8001/ingest/events", api_key="k123", timeout_s=2)._http_send([{"id": "1"}])
    assert seen["url"].endswith("/ingest/events") and seen["key"] == "k123" and seen["method"] == "POST"
    assert seen["ctype"] == "application/json" and seen["body"] == b'[{"id": "1"}]' and seen["timeout"] == 2


def test_stop_flushes_what_is_queued():
    got = []
    e = EventEmitter(sender=got.append, batch_size=100, flush_interval_s=0.3)
    e.start()
    for i in range(7):
        e.emit({"id": str(i)})
    e.stop(3.0)
    assert sum(len(b) for b in got) == 7


# ── event shape ─────────────────────────────────────────────────────────────────────────

def test_build_event_for_success_error_and_unmatched():
    ok = build_event("todo-app", "GET", "/todos", 200, 12.345)
    assert ok["level"] == "INFO" and ok["route"] == "GET /todos" and ok["duration_ms"] == 12.35
    assert ok["exception"] is None and ok["message"] == "GET /todos -> 200"
    err = build_event("todo-app", "POST", "/todos", 500, 5, "release=v2.4.0 pool=0/10", ValueError("boom"))
    assert err["level"] == "ERROR" and err["exception"] == "ValueError: boom"
    assert "release=v2.4.0" in err["message"] and err["message"].startswith("POST /todos -> 500 ValueError: boom")
    un = build_event("todo-app", "GET", None, 404, 1)
    assert un["route"] == "GET (unmatched)" and un["level"] == "WARNING"
    assert len({build_event("s", "GET", "/x", 200, 1)["id"] for _ in range(50)}) == 50  # unique ids


# ── middleware ──────────────────────────────────────────────────────────────────────────

class Collector:
    def __init__(self):
        self.service = "todo-app"
        self.events = []

    def emit(self, e):
        self.events.append(e)


def make_app(collector, boom=False):
    app = FastAPI()
    install(app, collector, context_fn=lambda: "release=v2.3.1", status_for_exc=lambda e: getattr(e, "status_code", 500))

    @app.get("/todos")
    def list_():
        return []

    @app.get("/todos/{todo_id}")
    def one(todo_id: int):
        if todo_id == 404:
            raise HTTPException(status_code=404, detail="nope")
        if todo_id == 503:
            raise PoolExhausted("connection pool exhausted")
        if todo_id == 500:
            raise ValueError("Simulated database state error")
        return {"id": todo_id}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/chaos")
    def chaos():
        return {}

    @app.exception_handler(Exception)
    async def handler(request, exc):
        return JSONResponse(status_code=exc.status_code if isinstance(exc, FaultError) else 500, content={"detail": str(exc)})

    return TestClient(app, raise_server_exceptions=False)


def test_route_template_is_used_not_the_raw_path():
    c = Collector()
    cl = make_app(c)
    for i in (1, 2, 3):
        assert cl.get(f"/todos/{i}").status_code == 200
    routes = {e["route"] for e in c.events}
    assert routes == {"GET /todos/{todo_id}"}          # bounded cardinality


def test_status_codes_for_http_errors_and_unhandled_faults():
    c = Collector()
    cl = make_app(c)
    assert cl.get("/todos/404").status_code == 404
    assert cl.get("/todos/503").status_code == 503
    assert cl.get("/todos/500").status_code == 500
    by_status = {e["status"]: e for e in c.events}
    assert set(by_status) == {404, 503, 500}
    assert by_status[503]["exception"].startswith("PoolExhausted")
    assert by_status[500]["level"] == "ERROR" and "ValueError" in by_status[500]["exception"]
    assert by_status[404]["level"] == "WARNING" and by_status[404]["exception"] is None
    assert all("release=v2.3.1" in e["message"] for e in c.events)


def test_only_todos_routes_are_emitted():
    c = Collector()
    cl = make_app(c)
    cl.get("/health")
    cl.get("/chaos")
    cl.get("/todos")
    assert [e["route"] for e in c.events] == ["GET /todos"]


def test_a_failing_context_function_never_breaks_the_request():
    c = Collector()
    app = FastAPI()
    install(app, c, context_fn=lambda: 1 / 0)

    @app.get("/todos")
    def t():
        return {"ok": True}

    r = TestClient(app).get("/todos")
    assert r.status_code == 200 and c.events[0]["message"] == "GET /todos -> 200"


def test_duration_is_recorded_in_milliseconds():
    c = Collector()
    app = FastAPI()
    install(app, c)

    @app.get("/todos")
    def slow():
        time.sleep(0.05)
        return []

    TestClient(app).get("/todos")
    assert 40 <= c.events[0]["duration_ms"] < 1000
