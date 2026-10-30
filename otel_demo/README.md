# Otel Test Application — AI SRE Copilot Testbed

A plug-and-play testbed for the [AI SRE Copilot](https://github.com/satwikt/incident_response_agent), built on the [OpenTelemetry Astronomy Shop demo](https://github.com/open-telemetry/opentelemetry-demo).

The testbed runs the Astronomy Shop microservices with OpenTelemetry instrumentation, collects traces, metrics, and logs through LGTM (Loki, Grafana, Tempo, Jaeger), and feeds correlated incident evidence to the Copilot ingest endpoint through a read-only adapter.

## Layout

| Path | Purpose |
|---|---|
| `integration/astronomy-shop/` | The test module: Compose overlay, Collector config, Grafana datasources/alerts/dashboard, adapter, scenario runner, tests |
| `vendor/opentelemetry-demo/` | The upstream Astronomy Shop application, vendored at `85c2cb2` (pin recorded in `vendor/opentelemetry-demo/.pinned-commit`) |
| `docs/` | Design, requirements, verification matrix, runbook, and handoff record |
| `reports/` | Generated testbed brief (Markdown and PDF) |

## Quick start

The module has no third-party Python dependency and needs no Docker for its local checks:

```bash
cd integration/astronomy-shop
make check      # 139-test suite, corpus freshness, local preflight
make dry-run    # validate the T-02 scenario plan without touching flagd-ui
make help       # all targets
```

To run the live stack, see [docs/live-validation.md](docs/live-validation.md) and [docs/operations-runbook.md](docs/operations-runbook.md).

## Documentation

- [docs/README.md](docs/README.md) — index of every design and delivery document
- [docs/HANDOFF.md](docs/HANDOFF.md) — current state and next actions
- [docs/requirements.md](docs/requirements.md) — functional, telemetry, security, and NFR requirements
- [docs/architecture.md](docs/architecture.md) — topology and component responsibilities
- [docs/integration-contract.md](docs/integration-contract.md) — the ingest boundary the adapter satisfies
- [docs/test-scenarios.md](docs/test-scenarios.md) — the T-01…T-10 fault curriculum
- [docs/operations-runbook.md](docs/operations-runbook.md) — start, inject, recover, reset
- [docs/VERIFICATION-MATRIX.md](docs/VERIFICATION-MATRIX.md) — requirement → artifact → check → status

## Upstream

The vendored application is unmodified upstream code from `open-telemetry/opentelemetry-demo`. It is not owned by this project; see its own `LICENSE` and `AGENTS.md` for terms and contribution rules.
