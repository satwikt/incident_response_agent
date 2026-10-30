# Requirements

## 1. Goal

Provide a realistic, repeatable, observable application environment in which the AI SRE Copilot can detect incidents, gather evidence from logs/metrics/traces/documentation, identify the failing component or dependency, distinguish recurring incidents from changed-context incidents, and use Hindsight memory safely.

## 2. Scope

> **Current delivery boundary (D-18):** This document records the broader integration vision. The delivered artifact is only the standalone test module under `integration/astronomy-shop/`. Requirements involving Copilot source changes, its documentation index, native-tool registration, Hindsight behavior, or a live external runtime are future integration goals, not current module requirements.

### In scope for the test module

- OpenTelemetry Astronomy Shop Demo deployment.
- Docker Compose first; Kubernetes later.
- Native flagd/OpenFeature fault injection.
- OpenTelemetry Collector routing.
- Prometheus-compatible metrics.
- Grafana dashboards and datasource correlation.
- Loki logs.
- Tempo traces.
- Jaeger traces retained as a second trace view.
- Official Astronomy Shop documentation as a portable corpus artifact.
- Adapter compatible with the Copilot's existing `POST /ingest/events` contract.
- Repeatable native fault scenarios, dry runs, and run records.
- Automated local tests, smoke checks, and a live-validation checklist.

### Out of scope for the first implementation

- Rewriting Astronomy Shop services.
- Autonomous remediation.
- Kubernetes-specific chaos as the initial path.
- Production hardening of the entire Astronomy Shop platform.
- Replacing Hindsight.
- Replacing the Copilot incident lifecycle.
- Supporting every possible observability backend.
- Copilot repository changes, native-tool registration, or documentation-index wiring.
- Hindsight changes, remediation behavior, or a Copilot runtime deployment.

## 3. Functional requirements

| ID | Requirement | Priority | Verification |
|---|---|---:|---|
| FR-01 | Start the selected application with documented Compose commands. | Must | Smoke command and status capture |
| FR-02 | Generate continuous baseline traffic. | Must | Load-generator health and telemetry |
| FR-03 | Enable and disable native flagd fault scenarios at runtime. | Must | Flag API/UI plus recovery check |
| FR-04 | Produce correlated logs, metrics, and traces for baseline and fault traffic. | Must | Backend queries and trace/log pivots |
| FR-05 | Preserve service, route/operation, status, duration, exception, and correlation identifiers in the adapter event model. | Must | Contract tests |
| FR-06 | Deliver normalized telemetry to Copilot `POST /ingest/events`. | Must | Ingest acceptance and SQLite query |
| FR-07 | Detect error-rate, latency, slow-request, keyword, and health breaches. | Must | Existing Copilot watcher tests plus live scenario |
| FR-08 | Identify the affected service and dependency edge. | Must | RCA evidence includes service graph path |
| FR-09 | Distinguish the same symptom with different causes. | Must | `paymentFailure` vs `paymentUnreachable` |
| FR-10 | Retain a resolved incident in Hindsight only from trusted structured evidence. | Must | Hindsight document and provenance |
| FR-11 | Recall a repeated incident and run context verification before proposing a remembered fix. | Must | Repeat scenario and model/tool record |
| FR-12 | Reject stale memory when runtime context differs. | Must | Changed-context scenario |
| FR-13 | Support novel cascading incidents requiring multi-signal investigation. | Should | Database/Kafka scenario |
| FR-14 | Index official application documentation for architecture and runbook questions. | Must | RAG retrieval test |
| FR-15 | Keep remediation actions human-approved and server allow-listed. | Must | Security and negative tests |

## 4. Telemetry requirements

### Metrics

- Prometheus-compatible ingestion.
- Request rate, error rate, duration, and service/resource metrics.
- Span-derived RED metrics where available.
- Exemplars or an equivalent metric-to-trace path where supported.
- Labels must include service identity and stable operation dimensions.
- Avoid unbounded labels such as raw user IDs, full URLs, or trace IDs as metric labels.

### Logs

- Structured or parseable log records.
- `service.name` and deployment metadata.
- `trace_id` and `span_id` where the source service supports log correlation.
- Exception type and safe diagnostic message.
- No secrets or raw payment data in the Copilot prompt or Hindsight narrative.

### Traces

- OTLP ingestion through the Collector.
- W3C trace context for HTTP/gRPC paths.
- Explicit handling/documentation for Kafka producer/consumer correlation.
- Stable `service.name`, operation names, status, duration, and dependency attributes.
- Export to Tempo and Jaeger.

## 5. Documentation requirements

The indexed corpus must include:

- System architecture.
- Service catalog.
- Dependency graph.
- Protocols and ports.
- Telemetry attributes and semantic conventions.
- Feature flag descriptions.
- Fault scenario runbooks.
- Deployment and recovery instructions.
- Known limitations, especially signal coverage gaps.

## 6. Security requirements

- Observability tools are read-only.
- Application logs and recalled memories are untrusted data, never instructions.
- No shell or arbitrary HTTP tool is exposed to the model.
- Copilot remediation remains allow-listed and human-approved.
- Credentials stay in environment variables or secret stores.
- Loki, Tempo, Jaeger, Prometheus, Grafana, and flagd control endpoints are not exposed publicly by default.
- PII and secrets are redacted before prompt construction and Hindsight retention.

## 7. Non-functional requirements

- Configuration changes must be reproducible from version-controlled files.
- Baseline deployment must be resettable.
- Fault scenarios must be deterministic enough for repeated evaluation.
- Telemetry loss must be visible as a degraded dependency, not silently treated as application health.
- The adapter must not block application request paths.
- Documentation must identify verified versus inferred behavior.
- A clean baseline must be restorable between test cases.

## 8. Acceptance gates

The integration is ready for regular evaluation only when:

1. Baseline traffic produces metrics, logs, and traces.
2. One native error scenario is detected and diagnosed.
3. The same scenario repeats and exercises Hindsight recall.
4. A similar symptom with changed context is not incorrectly resolved from stale memory.
5. A latency scenario identifies the downstream service.
6. A multi-hop trace can be pivoted to related logs.
7. Documentation retrieval returns the relevant service/fault runbook.
8. Collector/backend failure produces an explicit degraded-telemetry result.
9. All commands and results are recorded in `status.md` and `change-log.md`.
