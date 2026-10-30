"""Resource-pressure detection: a memory leak raises no errors, so it is judged on process memory."""

from types import SimpleNamespace

from agent import tools
from agent.detect import find_breaches

EMPTY = {}


def _t(limit=200.0):
    return SimpleNamespace(error_rate_pct=10.0, latency_p95_ms=500, slow_request_count=3, slow_request_min_ms=500,
                           log_scan_minutes=1, memory_alert_mb=limit)


def _find(resource, limit=200.0):
    return find_breaches({"status": "up"}, EMPTY, EMPTY, EMPTY, EMPTY, _t(limit), resource)


def test_memory_over_threshold_is_a_resource_breach():
    out = _find({"processes": [{"name": "catalog", "rss_mb": 420.0}, {"name": "checkout", "rss_mb": 55.0}]})
    assert [b.kind for b in out] == ["resource"]
    assert "catalog" in out[0].text and "420MB" in out[0].text


def test_memory_below_threshold_is_quiet():
    assert _find({"processes": [{"name": "catalog", "rss_mb": 60.0}]}) == []


def test_missing_resource_data_changes_nothing():
    assert _find(None) == []
    assert _find({}) == []


def test_threshold_zero_disables_the_check():
    assert _find({"processes": [{"name": "catalog", "rss_mb": 9000.0}]}, limit=0) == []


def test_get_resource_usage_parses_prometheus_and_fails_open(monkeypatch):
    payload = {"status": "success", "data": {"result": [
        {"metric": {"job": "astronomy-catalog"}, "value": [0, str(300 * 1024 * 1024)]}]}}
    monkeypatch.setattr(tools, "_http_get_json", lambda url, timeout=5.0: payload)
    assert tools.get_resource_usage()["processes"] == [{"name": "catalog", "rss_mb": 300.0}]

    def boom(url, timeout=5.0):
        raise OSError("prometheus down")
    monkeypatch.setattr(tools, "_http_get_json", boom)
    assert tools.get_resource_usage()["processes"] == []


def _find_logs(logs, limit=3):
    t = _t()
    t.error_log_count = limit
    return find_breaches({"status": "up"}, EMPTY, EMPTY, EMPTY, EMPTY, t, None, logs)


def test_error_log_burst_is_an_exception_breach():
    out = _find_logs({"count": 4, "sample_lines": ["ERROR | KeyError: sku | "]})
    assert [b.kind for b in out] == ["exception"]
    assert "4 application error" in out[0].text and "KeyError" in out[0].text


def test_few_or_no_error_logs_are_quiet():
    assert _find_logs({"count": 2, "sample_lines": []}) == []
    assert _find_logs(None) == []
    assert _find_logs({"count": 99, "sample_lines": []}, limit=0) == []


def test_error_logs_query_counts_only_non_request_errors(tmp_path, monkeypatch):
    from db import events as store
    now = store.now_ms()
    rows = [
        {"level": "ERROR", "message": "boom in worker", "exception": "ValueError: bad"},
        {"level": "ERROR", "message": "again", "exception": None},
        {"level": "ERROR", "message": "request failed", "route": "POST /x", "status": 500},   # request event: not counted
        {"level": "INFO", "message": "fine"},
        {"level": "ERROR", "message": "t | source=tempo"},                                       # adapter noise: not counted
    ]
    store.insert_events("svc", [{"id": f"e{i}", "ts": now, **r} for i, r in enumerate(rows)])
    res = store.error_logs("svc", 5)
    assert res["count"] == 2
    assert all("request failed" not in s for s in res["sample_lines"])


def _resource_incident():
    from agent import cycle
    from db import incidents
    inc = incidents.create("svc", [{"fp": "fp-r", "kind": "resource", "route": None, "sig": "resource"}], "warning",
                           ["Memory of 'catalog' is 432MB"], 1_000)
    incidents.set_rca(inc["id"], "Root cause: leak.\nProposed action: restart_worker", "ok", 1, 100)
    return cycle, incidents, inc["id"]


def test_restart_dropped_when_memory_already_back_to_normal(monkeypatch):
    cycle, incidents, iid = _resource_incident()
    monkeypatch.setattr(cycle.tools, "get_resource_usage",
                        lambda: {"processes": [{"name": "catalog", "rss_mb": 70.0}]})
    cycle.on_diagnosis_done(iid, 2_000)
    inc = incidents.get(iid)
    assert inc["proposed_action"] is None
    assert "Live re-check" in inc["rca_text"] and inc["llm_calls"] == 1


def test_restart_kept_while_memory_is_still_high(monkeypatch):
    cycle, incidents, iid = _resource_incident()
    monkeypatch.setattr(cycle.tools, "get_resource_usage",
                        lambda: {"processes": [{"name": "catalog", "rss_mb": 432.0}]})
    cycle.on_diagnosis_done(iid, 2_000)
    assert incidents.get(iid)["proposed_action"] == "restart_worker"


def test_restart_kept_when_memory_reading_unavailable(monkeypatch):
    cycle, incidents, iid = _resource_incident()
    monkeypatch.setattr(cycle.tools, "get_resource_usage", lambda: {"processes": []})
    cycle.on_diagnosis_done(iid, 2_000)
    assert incidents.get(iid)["proposed_action"] == "restart_worker"

