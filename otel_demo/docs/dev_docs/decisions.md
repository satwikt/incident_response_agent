# Architecture Decisions

| ID | Decision | Reason |
|---|---|---|
| D-01 | Use Astronomy Shop as primary testbed. | Best combination of OTel, docs, branching, and native faults. |
| D-02 | Start in minimal Compose mode. | Faster, cheaper, fewer unrelated failures. |
| D-03 | Keep the upstream application unmodified initially. | Preserve reproducibility and reduce maintenance. |
| D-04 | Use native flagd faults before external chaos. | Deterministic, reversible, semantically meaningful. |
| D-05 | Retain Jaeger and add Tempo/Loki through deployment config. | Satisfies both required trace paths and Grafana correlation. |
| D-06 | Preserve the existing Copilot ingest boundary first. | Reduces integration risk and reuses tested incident lifecycle. |
| D-07 | Add native query tools after adapter validation. | Separate deployment/integration risk from agent behavior risk. |
| D-08 | Treat recalled memory as a hypothesis. | Prevent stale fixes from being applied to changed causes. |
| D-09 | No autonomous remediation in the first integration. | Safety and clear evaluation of diagnosis quality. |
| D-10 | Use full deployment only for Kafka scenarios. | Kafka adds resource cost not needed for basic RCA. |
| D-11 | Keep the upstream application under `vendor/opentelemetry-demo`; keep integration changes under `integration/astronomy-shop`. | Separates upstream code from Copilot-owned configuration and makes upgrades/reverts reproducible. |
| D-12 | Use deterministic evidence IDs for polled telemetry. | Repeated polls of the same trace/log/sample must dedupe in the ingest store instead of accumulating duplicates. |
| D-13 | Chunk ingest batches by count and serialized size. | The ingest contract caps bodies at 500 events and 1 MiB; count alone can still trigger 413. |
| D-14 | Presets carry an explicit service override. | Aggregate PromQL drops labels, so evidence conversion must not guess a missing `service_name`. |
| D-15 | Control scenarios through the flagd-ui read/write API with snapshot/restore. | It is the vendored, documented path; direct vendor-file edits are forbidden and the snapshot guarantees recovery. |
| D-16 | Native tools are read-only and separate from ingest. | Detection stays push-based and tool actions cannot write flags or remediate. |
| D-17 | Derive alerts, dashboards, and presets from vendored metric names. | Avoids invented signals and keeps config aligned with the pinned demo. |
| D-18 | Deliver only a plug-and-play Astronomy Shop test module; do not modify the Copilot repository. | Keeps the integration portable and independently testable; Copilot-side tools, index wiring, and live runtime work remain separate concerns. |

## Rejected alternatives

- **Spring Petclinic:** simpler, but not enough integrated OTel/Loki/Tempo/Jaeger support.
- **Online Boutique:** good topology, weaker native fault curriculum.
- **PostHog:** realistic but heavy, mutable, and difficult to fault deterministically.
- **Train Ticket:** too large and legacy-heavy for the first integration.
- **Archived Grafana OTel forks:** not suitable as the maintained source of truth.

## Open decisions

- Separate Loki/Tempo containers versus `grafana/otel-lgtm` plus Jaeger.
- Exact supported upstream release pin.
- Whether the adapter remains long-term or is replaced fully by native tools.
