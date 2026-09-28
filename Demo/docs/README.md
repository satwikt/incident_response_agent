# Demo App

A production-grade **Todo REST API** with a built-in fault-injection framework, designed for SRE Copilot demonstrations.

---

## Overview

The Demo app is a FastAPI backend paired with a lightweight glassmorphism frontend. It exposes a standard Todo CRUD API and an `/admin/faults` control plane that lets you simulate real-world failure modes on demand — without touching any application code.

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (v24+)
- `docker compose` CLI

---

## Quick Start

```bash
cd Demo
docker compose up -d --build
```

The app will be available at **http://localhost:8000**.

To stop all services:

```bash
docker compose down
```

---

## Port Reference

| Service | URL                         |
|---------|-----------------------------|
| App     | http://localhost:8000        |

---

## Project Structure

```
Demo/
├── app/
│   ├── Dockerfile
│   ├── main.py           # FastAPI application, routes, middleware
│   ├── models.py         # SQLAlchemy ORM model + Pydantic schemas
│   ├── database.py       # DB engine setup and session factory
│   ├── faults.py         # Fault flag definitions and toggle logic
│   ├── requirements.txt
│   └── static/
│       └── index.html    # Frontend UI
├── docker-compose.yml
└── docs/                 # ← You are here
    ├── README.md
    ├── architecture.md
    ├── api-reference.md
    └── fault-injection.md
```

---

## Documentation Index

| Doc | Description |
|-----|-------------|
| [architecture.md](./architecture.md) | Application components, data model, and request lifecycle |
| [api-reference.md](./api-reference.md) | Full REST API reference with request/response examples |
| [fault-injection.md](./fault-injection.md) | Fault flags, their effects, and how to toggle them |
