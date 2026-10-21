# Recommended Test Application

## OpenTelemetry Astronomy Shop Demo

**Repository:** https://github.com/open-telemetry/opentelemetry-demo  
**Documentation:** https://opentelemetry.io/docs/demo/

## Decision

Use the **OpenTelemetry Astronomy Shop Demo** as the primary testbed for the AI SRE Copilot. Run its native feature-flag scenarios first, and add Grafana LGTM (Loki, Grafana, Tempo, Prometheus-compatible metrics) plus Jaeger through deployment configuration.

## Why this application

- **Real distributed behavior:** multiple services communicate over HTTP, gRPC, and Kafka, with PostgreSQL and Valkey dependencies.
- **Strong OpenTelemetry foundation:** the project is specifically maintained to demonstrate OpenTelemetry traces, metrics, logs, exemplars, dashboards, and telemetry tests.
- **Excellent documentation:** official architecture, service, telemetry, feature-flag, and debugging documentation is suitable for the Copilot's documentation/RAG tool.
- **Native, repeatable fault injection:** flagd/OpenFeature provides reversible runtime scenarios without changing application code.
- **Good RCA depth:** faults can be isolated, downstream, cohort-specific, database-related, asynchronous, resource-related, or AI-related.
- **Minimal application modification:** most work is limited to Collector, backend, datasource, and Copilot adapter configuration.

## Observability model

```text
Astronomy Shop services
        |
        v
OpenTelemetry Collector
   |          |          |
Prometheus  Tempo/Jaeger  Loki
        \      |      /
             Grafana
```

The upstream deployment already includes the application, Collector, Prometheus, Grafana, and Jaeger/OpenSearch paths. Loki and Tempo are added with a small Collector overlay or the Grafana `otel-lgtm` backend. Jaeger can be retained as a second trace backend.

## Native fault scenarios

| Scenario | RCA capability tested |
|---|---|
| `paymentFailure` | Repeatable known incident and memory recall |
| `paymentUnreachable` | Same symptom, different downstream cause |
| `intlShippingSlowdown` | Cohort/geography-specific latency |
| `productCatalogLockContention` | Database contention and cascading latency |
| `kafkaQueueProblems` | Async backpressure and consumer lag |
| `recommendationCacheFailure` | Gradual memory and latency degradation |
| `aiRunawayAgent` | AI/tool-loop failure diagnosis |

## Fit for the AI SRE Copilot

The application supports the Copilot workflow:

1. Prometheus threshold breach triggers proactive analysis.
2. Trace and log correlation narrows the failing service or dependency.
3. Official documentation explains expected service behavior.
4. Hindsight recalls previous incidents and successful fixes.
5. A repeat fault can take the fast memory-verification path.
6. A similar symptom with a different cause can be rejected as stale memory.

## Recommended rollout

1. Start with native flags: `paymentFailure`, `paymentUnreachable`, and `intlShippingSlowdown`.
2. Connect the Copilot to Prometheus, Loki, Tempo, and Jaeger using read-only tools.
3. Index the official demo documentation for RAG.
4. Add Kafka and database-lock scenarios after the basic RCA loop works.
5. Use Chaos Mesh or Istio later for infrastructure-level experiments; they are optional, not required initially.

## Conclusion

This is the strongest practical choice because it combines realistic distributed behavior, first-class OpenTelemetry support, rich documentation, deterministic native faults, and a low-risk integration path. It is more capable than a simple Petclinic-style application while avoiding the deployment and fault-control burden of a large production SaaS platform such as PostHog.
