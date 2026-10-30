# Implementation Plan

> **Delivery boundary (D-18):** This document preserves the broader integration roadmap. The completed deliverable is the plug-and-play test module under `integration/astronomy-shop/`; Copilot-repository changes, documentation-index ingestion, native-tool registration, and Hindsight work are outside that module. Live steps below are optional external validation, not prerequisites for its local delivery.

## Chunk 0 — Documentation and baseline

Deliverables:

- This documentation set.
- Clean workspace/repository baseline.
- Recorded upstream commit/version.

Exit: requirements and decisions reviewed.

## Chunk 1 — Astronomy Shop deployment

Actions:

1. Clone/pin Astronomy Shop version.
2. Start minimal Compose mode.
3. Confirm frontend, load generator, Collector, Prometheus, Grafana, and Jaeger.
4. Capture resource and port requirements.

Exit: baseline traffic and health checks pass.

## Chunk 2 — LGTM overlay

Actions:

1. Add `grafana/otel-lgtm` or separate Loki/Tempo services.
2. Extend Collector exporters.
3. Preserve upstream `span_metrics`/required pipeline components.
4. Provision Grafana Prometheus, Loki, Tempo, and Jaeger datasources.
5. Configure trace-to-log and exemplar links.

Exit: one trace, related logs, and metrics are queryable.

## Chunk 3 — Documentation corpus

Actions:

1. Mirror official architecture/service/telemetry/feature-flag docs.
2. Add local deployment and fault runbooks.
3. Add metadata: source URL, application version, service, scenario.
4. Index into the Copilot documentation mechanism.

Exit: architecture and scenario queries retrieve relevant documents.

## Chunk 4 — Copilot adapter

Actions:

1. Implement read-only backend clients.
2. Normalize evidence to `/ingest/events`.
3. Add bounded batching/retry.
4. Add service-name and operation mapping.
5. Add trace/log identifiers to event context.

Exit: Copilot SQLite contains Astronomy Shop evidence and existing watcher detects a fault.

## Chunk 5 — Native fault curriculum

Run, in order:

1. `paymentFailure`
2. `paymentUnreachable`
3. `intlShippingSlowdown`
4. `recommendationCacheFailure`
5. `productCatalogLockContention`
6. Full-mode `kafkaQueueProblems`

Exit: baseline, repeat, changed-context, latency, and cascade tests pass.

## Chunk 6 — Native observability tools

Replace broad adapter polling with targeted Copilot tools for Prometheus, Loki, Tempo, Jaeger, flagd, and docs.

Exit: RCA cites backend evidence and trace IDs directly.

## Chunk 7 — External chaos and hardening

Only after native scenarios work, add optional Chaos Mesh/Istio experiments, security checks, resource limits, and failure-of-observability tests.
