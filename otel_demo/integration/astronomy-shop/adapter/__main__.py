"""Run the read-only adapter polling loop.

Example:
    COPILOT_API_KEY=... python3 -m adapter \
        --ingest-url http://localhost:8000/ingest/events \
        --preset checkout-error-ratio --preset checkout-error-logs \
        --tempo-service checkout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Optional, Sequence

from .jaeger import JaegerClient
from .loki import LokiClient
from .poller import MAX_BATCH_SIZE, Poller, RetryPolicy, parse_ingest_keys
from .presets import PRESETS, presets_by_backend
from .prometheus import PrometheusClient
from .sources import (
    jaeger_source,
    loki_source,
    prometheus_source,
    tempo_source,
)
from .tempo import TempoClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll Astronomy Shop telemetry into Copilot ingest.")
    parser.add_argument("--ingest-url", default=os.getenv("COPILOT_INGEST_URL"))
    parser.add_argument("--api-key", default=os.getenv("COPILOT_API_KEY"))
    parser.add_argument(
        "--ingest-keys",
        default=os.getenv("COPILOT_INGEST_KEYS", ""),
        help="service:key pairs (comma separated); one request is sent per service",
    )
    parser.add_argument(
        "--watch-service",
        default=os.getenv("COPILOT_WATCH_SERVICE", ""),
        help=(
            "the single service the Copilot watches (its APP_SERVICE_NAME); "
            "evidence for any other service is dropped"
        ),
    )
    parser.add_argument("--prometheus-url", default=os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    parser.add_argument("--prometheus-query", action="append", default=[])
    parser.add_argument("--loki-url", default=os.getenv("LOKI_URL", "http://localhost:3100"))
    parser.add_argument("--loki-query", action="append", default=[])
    parser.add_argument("--tempo-url", default=os.getenv("TEMPO_URL", "http://localhost:3200"))
    parser.add_argument("--tempo-service", action="append", default=[])
    parser.add_argument(
        "--jaeger-url",
        default=os.getenv("JAEGER_URL", "http://localhost:8080/jaeger/ui"),
        help="Jaeger base URL; the upstream proxy serves it under /jaeger/ui",
    )
    parser.add_argument(
        "--jaeger-api-prefix",
        default=os.getenv("JAEGER_API_PREFIX", "/api"),
        help="path prefix for the Jaeger query API, appended to --jaeger-url",
    )
    parser.add_argument("--jaeger-service", action="append", default=[])
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        help="named query preset (see --list-presets); repeatable",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="print query presets and exit",
    )
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--window", type=float, default=300.0)
    parser.add_argument("--batch-size", type=int, default=MAX_BATCH_SIZE)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    return parser


def _print_presets() -> None:
    for preset in PRESETS.values():
        print(f"{preset.name:28} {preset.backend:10} {preset.description}")


def build_sources(args: argparse.Namespace) -> dict[str, object]:
    sources: dict[str, object] = {}

    prometheus_queries = [
        (f"prometheus:{index}", query, None)
        for index, query in enumerate(args.prometheus_query, start=1)
    ]
    prometheus_queries += [
        (f"prometheus:{preset.name}", preset.query, preset.service)
        for preset in presets_by_backend("prometheus")
        if preset.name in args.preset
    ]
    if prometheus_queries:
        client = PrometheusClient(args.prometheus_url, timeout=args.timeout)
        for key, query, service in prometheus_queries:
            sources[key] = prometheus_source(client, query, service=service)

    loki_queries = [
        (f"loki:{index}", query, None)
        for index, query in enumerate(args.loki_query, start=1)
    ]
    loki_queries += [
        (f"loki:{preset.name}", preset.query, preset.service)
        for preset in presets_by_backend("loki")
        if preset.name in args.preset
    ]
    if loki_queries:
        client = LokiClient(args.loki_url, timeout=args.timeout)
        for key, query, service in loki_queries:
            sources[key] = loki_source(
                client, query, window_seconds=args.window, service=service
            )

    if args.tempo_service:
        client = TempoClient(args.tempo_url, timeout=args.timeout)
        for service in args.tempo_service:
            sources[f"tempo:{service}"] = tempo_source(
                client, service, window_seconds=args.window
            )
    if args.jaeger_service:
        client = JaegerClient(
            args.jaeger_url, api_prefix=args.jaeger_api_prefix, timeout=args.timeout
        )
        for service in args.jaeger_service:
            sources[f"jaeger:{service}"] = jaeger_source(
                client, service, window_seconds=args.window
            )
    return sources


def _configured_services(args: argparse.Namespace) -> dict[str, list[str]]:
    """Map each explicitly configured service to the source keys that target it.

    Only services named by the operator are known ahead of a poll; a raw
    Prometheus or Loki query resolves its service from the data, so it is not
    listed here.
    """
    services: dict[str, list[str]] = {}
    for preset in presets_by_backend("prometheus") + presets_by_backend("loki"):
        if preset.name in args.preset:
            services.setdefault(preset.service, []).append(f"preset:{preset.name}")
    for service in args.tempo_service:
        services.setdefault(service, []).append(f"tempo:{service}")
    for service in args.jaeger_service:
        services.setdefault(service, []).append(f"jaeger:{service}")
    return services


def _warn_unwatched(args: argparse.Namespace) -> None:
    """Report sources that target a service the Copilot does not watch."""
    watch = (args.watch_service or "").strip()
    if not watch:
        return
    for service, keys in sorted(_configured_services(args).items()):
        if service != watch:
            print(
                f"warning: {', '.join(keys)} target service {service!r}, but only "
                f"{watch!r} is watched; that evidence will be dropped",
                file=sys.stderr,
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.list_presets:
        _print_presets()
        return 0

    unknown = [name for name in args.preset if name not in PRESETS]
    if unknown:
        parser.error(
            "unknown preset(s): " + ", ".join(unknown) + "; see --list-presets"
        )
    if not args.ingest_url:
        parser.error("--ingest-url or COPILOT_INGEST_URL is required")
    if not args.api_key and not args.ingest_keys:
        parser.error(
            "--api-key (COPILOT_API_KEY) is required unless --ingest-keys "
            "(COPILOT_INGEST_KEYS) is set"
        )

    sources = build_sources(args)
    if not sources:
        parser.error(
            "configure at least one source: --prometheus-query, --loki-query, "
            "--tempo-service, --jaeger-service, or --preset"
        )

    _warn_unwatched(args)

    poller = Poller(
        args.ingest_url,
        args.api_key or "",
        batch_size=args.batch_size,
        timeout=args.timeout,
        policy=RetryPolicy(attempts=args.attempts),
        service_keys=parse_ingest_keys(args.ingest_keys),
        watch_service=args.watch_service or None,
    )
    cycles = 1 if args.once else None
    try:
        for report in poller.run_forever(
            sources, interval=args.interval, max_cycles=cycles
        ):
            print(json.dumps(asdict(report), sort_keys=True), flush=True)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
