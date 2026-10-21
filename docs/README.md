# AI Incident Response Agent: Documentation Index

Welcome to the comprehensive documentation for the **AI Incident Response Agent** testbed, built for the NVIDIA Hackathon on the **OpenTelemetry Astronomy Shop** microservices architecture.

---

## Documentation Index

| Document | Purpose |
| :--- | :--- |
| [**Architecture Overview**](ARCHITECTURE.md) | High-level system topology, 3-Agent architecture, NVIDIA Nemotron tiers, Laya decision engine, and LGTM observability pipeline. |
| [**Operations Runbook**](OPERATIONS_RUNBOOK.md) | Step-by-step operator guide for starting the unified Docker stack, injecting faults via Flagd, viewing the Incident Dashboard, and verifying fixes. |
| [**Observability Tools & Security**](OBSERVABILITY_TOOLS.md) | Specification of the 15 parameterized, injection-proof telemetry tools, Prometheus/Loki/Tempo templates, and RAG document search. |
| [**Legacy Documentation Archive**](legacy/) | Archived design documents, test logs, defect trackers, and scripts from early project iterations. |

---

## Core Components

```
                     ┌─────────────────────────────────────────────────────────┐
                     │           OpenTelemetry Astronomy Shop Demo             │
                     │  (checkout, payment, shipping, frontend, cart, ...)      │
                     └────────────────────────────┬────────────────────────────┘
                                                  │ Telemetry (OTLP)
                                                  ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │            OpenTelemetry Collector & LGTM               │
                     │     Prometheus (9090) │ Loki (3100) │ Tempo (3200)      │
                     └─────────────┬───────────────────────────┬───────────────┘
                                   │ Raw Telemetry             │ Normalized Events
                                   │                           ▼
                                   │               ┌───────────────────────┐
                                   │               │   Telemetry Adapter   │
                                   │               └───────────┬───────────┘
                                   │                           │ POST /ingest/events
                                   ▼                           ▼
                     ┌─────────────────────────────────────────────────────────┐
                     │                   AI SRE Copilot                        │
                     │  ┌──────────────────┐ ┌──────────────┐ ┌──────────────┐ │
                     │  │ Proactive Agent  │ │ Reactive RCA │ │ Reactive Qry │ │
                     │  │ (Alerts/Watcher) │ │ (Diagnostician)│ (Telemetry)  │ │
                     │  └──────────────────┘ └──────┬───────┘ └──────────────┘ │
                     │                              │                          │
                     │              ┌───────────────┴───────────────┐          │
                     │              │ Laya System 1 Decision Engine │          │
                     │              └───────────────┬───────────────┘          │
                     │                              │                          │
                     │              ┌───────────────┴───────────────┐          │
                     │              │ NVIDIA Nemotron Model Tiers   │          │
                     │              │  (Nano  │  Super  │  Ultra)   │          │
                     │              └───────────────┬───────────────┘          │
                     └──────────────────────────────┼──────────────────────────┘
                                                    │ Recall & Retain
                                                    ▼
                                     ┌─────────────────────────────┐
                                     │   Hindsight Agent Memory    │
                                     │  (Episodic & Procedural)    │
                                     └─────────────────────────────┘
```

---

## Quick Reference Ports

* **Incident Copilot Chat UI:** `http://localhost:8001/`
* **Incident Copilot Dashboard:** `http://localhost:8001/dashboard`
* **Astronomy Shop Web Store:** `http://localhost:8080/`
* **Flagd Feature Flag Control:** `http://localhost:8080/feature`
* **Grafana Dashboards:** `http://localhost:3001/`
* **Prometheus API & UI:** `http://localhost:9090/`
* **Hindsight Memory UI:** `http://localhost:9999/`
