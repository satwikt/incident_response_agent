"""OpenTelemetry tracing, Prometheus metrics, and structured logging setup."""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import urllib.request
from typing import Callable

from fastapi import FastAPI, Request, Response

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
except ImportError:
    class _DummyMetric:
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass

    def Counter(*args, **kwargs):
        return _DummyMetric()

    def Histogram(*args, **kwargs):
        return _DummyMetric()

    def generate_latest():
        return b""

    CONTENT_TYPE_LATEST = "text/plain"

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except ImportError:
    class _DummySpan:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def set_attribute(self, *args):
            pass
        def get_span_context(self):
            return None

    class _DummyTracer:
        def start_as_current_span(self, *args, **kwargs):
            return _DummySpan()

    class _DummyTrace:
        def get_tracer(self, *args, **kwargs):
            return _DummyTracer()
        def get_current_span(self):
            return None
        def set_tracer_provider(self, *args):
            pass

    trace = _DummyTrace()
    Resource = TracerProvider = BatchSpanProcessor = OTLPSpanExporter = None

# Prometheus metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the service",
    ["service", "method", "route", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["service", "method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


class JsonLogFormatter(logging.Formatter):
    """Format logs as structured JSON including current OpenTelemetry trace & span IDs."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        current_span = trace.get_current_span()
        ctx = current_span.get_span_context() if current_span else None
        trace_id = format(ctx.trace_id, "032x") if ctx and ctx.trace_id else ""
        span_id = format(ctx.span_id, "016x") if ctx and ctx.span_id else ""

        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "service": self.service_name,
            "message": record.getMessage(),
            "logger": record.name,
            "trace_id": trace_id,
            "span_id": span_id,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class LokiLogHandler(logging.Handler):
    """Asynchronous, non-blocking log handler that streams structured logs to Grafana Loki."""

    def __init__(self, loki_url: str, service_name: str, max_queue: int = 5000, batch_size: int = 50, flush_interval: float = 0.5) -> None:
        super().__init__()
        self.loki_url = loki_url.rstrip("/")
        self.service_name = service_name
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._q: queue.Queue = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name=f"loki-log-{service_name}", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            current_span = trace.get_current_span()
            ctx = current_span.get_span_context() if current_span else None
            trace_id = format(ctx.trace_id, "032x") if ctx and getattr(ctx, "trace_id", None) else ""
            span_id = format(ctx.span_id, "016x") if ctx and getattr(ctx, "span_id", None) else ""

            msg_payload = {
                "timestamp": self.formatter.formatTime(record) if self.formatter else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                "level": record.levelname,
                "service": self.service_name,
                "message": record.getMessage(),
                "logger": record.name,
                "trace_id": trace_id,
                "span_id": span_id,
            }
            if record.exc_info and self.formatter:
                msg_payload["exception"] = self.formatter.formatException(record.exc_info)

            ts_ns = int(record.created * 1e9)
            line = json.dumps(msg_payload)
            self._q.put_nowait((record.levelname, ts_ns, line))
        except Exception:
            pass  # Fails open: never block application processing

    def _worker(self) -> None:
        while not self._stop.is_set():
            batch = []
            deadline = time.monotonic() + self.flush_interval
            while len(batch) < self.batch_size:
                timeout = max(0.01, deadline - time.monotonic())
                try:
                    item = self._q.get(timeout=timeout)
                    batch.append(item)
                except queue.Empty:
                    break
            if batch:
                self._send_batch(batch)

    def _send_batch(self, batch: list) -> None:
        grouped: dict[str, list] = {}
        for level, ts_ns, line in batch:
            grouped.setdefault(level, []).append([str(ts_ns), line])

        streams = []
        for level, values in grouped.items():
            streams.append({
                "stream": {
                    "service_name": self.service_name,
                    "level": level,
                },
                "values": values,
            })

        payload = json.dumps({"streams": streams}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.loki_url}/loki/api/v1/push",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0):
                pass
        except Exception:
            pass  # Fails open: drop if Loki unavailable


def setup_telemetry(app: FastAPI, service_name: str) -> trace.Tracer:
    """Initialize OpenTelemetry tracer, Prometheus metrics endpoint, and structured logging."""
    # 1. Logging setup
    root_logger = logging.getLogger()
    stdout_handler = logging.StreamHandler(sys.stdout)
    formatter = JsonLogFormatter(service_name)
    stdout_handler.setFormatter(formatter)
    handlers: list[logging.Handler] = [stdout_handler]

    loki_url = os.getenv("LOKI_URL", "http://loki:3100")
    if loki_url:
        loki_handler = LokiLogHandler(loki_url, service_name)
        loki_handler.setFormatter(formatter)
        handlers.append(loki_handler)

    root_logger.handlers = handlers
    root_logger.setLevel(logging.INFO)

    # 2. OpenTelemetry Tracing
    if Resource and TracerProvider:
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        
        tempo_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://tempo:4318/v1/traces")
        try:
            otlp_exporter = OTLPSpanExporter(endpoint=tempo_endpoint)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        except Exception as exc:
            logging.getLogger(service_name).warning("Failed to initialize OTLP exporter (%s): continuing local", exc)

        trace.set_tracer_provider(provider)
    tracer = trace.get_tracer(service_name)

    # 3. Middleware for Request Metrics, Logging & Context Propagation
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next: Callable) -> Response:
        route_path = request.url.path
        start_time = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            logging.getLogger(service_name).error(
                "Unhandled exception handling %s %s: %s",
                request.method,
                route_path,
                exc,
                exc_info=True,
            )
            raise
        finally:
            duration = time.time() - start_time
            if route_path != "/metrics" and route_path != "/health":
                HTTP_REQUESTS_TOTAL.labels(
                    service=service_name,
                    method=request.method,
                    route=route_path,
                    status_code=str(status_code),
                ).inc()
                HTTP_REQUEST_DURATION_SECONDS.labels(
                    service=service_name,
                    method=request.method,
                    route=route_path,
                ).observe(duration)

                dur_ms = duration * 1000.0
                svc_logger = logging.getLogger(service_name)
                msg = f"{request.method} {route_path} -> {status_code} ({dur_ms:.1f}ms)"
                if status_code >= 500:
                    svc_logger.error(msg)
                elif status_code >= 400:
                    svc_logger.warning(msg)
                else:
                    svc_logger.info(msg)

    # 4. Expose /metrics
    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return tracer
