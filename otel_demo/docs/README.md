# AI SRE Copilot Testbed Documentation

This directory is the living design and delivery record for integrating the AI SRE Copilot with the selected test application.

## Selected application

**OpenTelemetry Astronomy Shop Demo**  
Repository: <https://github.com/open-telemetry/opentelemetry-demo>  
Documentation: <https://opentelemetry.io/docs/demo/>

**AI SRE Copilot**  
Repository: <https://github.com/satwikt/incident_response_agent>

The application is a polyglot, distributed e-commerce system with HTTP, gRPC, Kafka, PostgreSQL, Valkey, OpenTelemetry, Grafana, Prometheus, Jaeger, flagd, and documented fault scenarios.

## Documents

| Document | Purpose |
|---|---|
| [requirements.md](requirements.md) | Functional, telemetry, documentation, security, and non-functional requirements |
| [architecture.md](architecture.md) | Target topology and component responsibilities |
| [integration-contract.md](integration-contract.md) | Boundary between Astronomy Shop telemetry and the Copilot |
| [implementation-plan.md](implementation-plan.md) | Ordered phases, work chunks, deliverables, and exit criteria |
| [decisions.md](decisions.md) | Accepted decisions, rejected alternatives, and open decisions |
| [test-scenarios.md](test-scenarios.md) | Fault-injection curriculum and expected RCA evidence |
| [operations-runbook.md](operations-runbook.md) | Start, stop, inject, observe, recover, and troubleshoot procedures |
| [status.md](status.md) | Current implementation status and next action |
| [change-log.md](change-log.md) | Chronological record of research, implementation, and verification changes |
| [HANDOFF.md](HANDOFF.md) | Primary resume guide: state, artifacts, next action, estimates |
| [CODE-STRUCTURE.md](CODE-STRUCTURE.md) | Code map: architecture diagram, module layout, file-by-file walkthrough, where the traps are |
| [TEAM-GUIDE.md](TEAM-GUIDE.md) | How to work in this repo day to day: commands, conventions, debugging recipes, what not to do |
| [TEAM-HANDOFF.md](TEAM-HANDOFF.md) | Full handoff for the integration/completion owner: state, remaining work, acceptance criteria, inherited risks |
| [RESUME-PROMPT.md](RESUME-PROMPT.md) | Copy-paste prompt for the next AI agent |
| [IMPLEMENTATION-NOTES.md](IMPLEMENTATION-NOTES.md) | Architecture, module contracts, invariants, failure modes |
| [FILE-INVENTORY.md](FILE-INVENTORY.md) | File-by-file purpose and verification state |
| [VERIFICATION-MATRIX.md](VERIFICATION-MATRIX.md) | Requirement → artifact → check → status, with the test coverage map |
| [live-validation.md](live-validation.md) | First live-run checklist with expected outputs |
| [native-tools-spec.md](native-tools-spec.md) | Future Copilot read-only tool contract (reference only) |
| [native-tools.schema.json](native-tools.schema.json) | Machine-readable tool schemas and limits |

## How these documents are maintained

- `status.md` is updated after every implementation chunk.
- `change-log.md` records every material decision or code/configuration change.
- Requirements are not silently weakened; changes are recorded as amendments.
- Any claim marked **verified** must have a command, test, or live observation recorded.
- Any claim marked **planned** or **inferred** must not be reported as implemented.

## Current state

The documentation and research phase is complete. The deliverable is the plug-and-play Astronomy Shop test module: overlay, adapter, documentation corpus, scenario tooling, Grafana configuration, and local checks. It is implemented and locally verified with `make check`; live stack validation is an optional external step and is blocked in this worker because Docker is unavailable.

The Copilot project is identified at <https://github.com/satwikt/incident_response_agent>, but its runtime checkout is not present in this workspace. Its current telemetry path is push-based:

```text
Monitored application → POST /ingest/events → Copilot SQLite event store
```

The integration preserves this stable boundary through the adapter. The deliverable is the standalone test module; Copilot-side native query tools and documentation-index wiring are outside this module and are not implemented here.

## Working rule

The default path is configuration-first and read-only:

1. Do not modify Astronomy Shop business logic initially.
2. Add only Compose, Collector, Grafana datasource, adapter, documentation, and test assets.
3. Use native flagd scenarios before external Chaos Mesh or Istio experiments.
4. Keep all Copilot remediation actions human-approved and allow-listed.
