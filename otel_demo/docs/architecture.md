# Target Architecture

```text
Astronomy Shop services + flagd + load generator
                 |
                 | OTLP
                 v
        OpenTelemetry Collector
          |        |        |
      metrics    traces    logs
          |        |        |
      Prometheus Tempo/Jaeger Loki
          \        |        /
                 Grafana

Prometheus/Loki/Tempo/Jaeger + docs
                 |
                 v
          Copilot adapter/tools
                 |
                 v
          Copilot incident engine
       detection → diagnosis → Hindsight
```

## Components

- **Astronomy Shop:** unchanged application target.
- **flagd:** native runtime fault control.
- **Collector:** receives OTLP and fans out signals.
- **Prometheus:** metrics and threshold source.
- **Loki:** structured log query source.
- **Tempo:** primary trace query source.
- **Jaeger:** secondary trace UI/query source.
- **Grafana:** operator visualization and correlation.
- **Adapter:** converts selected normalized evidence into the Copilot ingest contract.
- **Copilot:** owns incidents, diagnosis, Hindsight, notifications, and approval workflow.

## Design principle

The Copilot must reason from externally observable evidence. It must not inspect Astronomy Shop source code during diagnosis.

## Deployment modes

1. **Minimal mode:** core synchronous scenarios and lower resource usage.
2. **Full mode:** Kafka, Accounting, Fraud Detection, and async scenarios.
3. **Agentic mode:** optional Astronomy Shop Agent/MCP/Chatbot services.

Use minimal mode first; enable full mode only for Kafka tests.
