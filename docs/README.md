# AI Incident Response Agent: Documentation Index

Welcome to the comprehensive documentation for the **AI Incident Response Agent** testbed, built for the NVIDIA Hackathon on the **OpenTelemetry Astronomy Shop** microservices architecture.

---

## Documentation Index

| Document | Purpose |
| :--- | :--- |
| [**Architecture Overview**](ARCHITECTURE.md) | High-level system topology, 3-Agent architecture, NVIDIA Nemotron tiers, Laya decision engine, and LGTM observability pipeline. |
| [**Operations Runbook**](OPERATIONS_RUNBOOK.md) | Step-by-step operator guide for RAG indexing, Docker build and startup, injecting faults via the Faults Control Deck, viewing the Incident Dashboard, and verifying fixes. |
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

## Setup & Execution Summary

1. **Configure Environment:** `cp .env.example .env` and insert your `NVIDIA_API_KEY` and `HINDSIGHT_LLM_API_KEY`.
2. **Build Documentation RAG Index:** `./scripts/index_docs.sh` (populates ChromaDB & BM25 index).
3. **Build Containers:** `docker compose build`
4. **Launch Stack:** `docker compose up -d`

---

## Quick Reference Ports

| Service / Interface | Host URL | Description |
| :--- | :--- | :--- |
| **Astronomy Shop Web Store** | `http://localhost:9922/` | Microservices storefront gateway |
| **Runtime Faults Control Deck** | `http://localhost:9922/faults` | Interactive fault injection console |
| **Incident Copilot Chat UI** | `http://localhost:9911/` | Multi-turn conversational operator agent |
| **Incident Copilot Dashboard** | `http://localhost:9911/dashboard` | Incident timeline & fix verification console |
| **Grafana Dashboards** | `http://localhost:3001/` | LGTM telemetry dashboards (`admin` / `admin`) |
| **Prometheus API & UI** | `http://localhost:9090/` | PromQL evaluator and metrics database |
| **Tempo Traces** | `http://localhost:3200/` | Distributed tracing search (`:4318` OTLP) |
| **Loki Logs** | `http://localhost:3100/` | Structured JSON log aggregator |
| **Hindsight Memory UI** | `http://localhost:9999/` | Episodic memory bank and recall inspector |
| **Catalog API** | `http://localhost:5001/` | Catalog & cart microservice |
| **Checkout API** | `http://localhost:5002/` | Checkout & order microservice |
| **PostgreSQL DB** | `localhost:5433` | PostgreSQL 16 database (internal `:5432`) |
