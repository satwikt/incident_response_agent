"""Starting-point queries derived from the vendored Astronomy Shop dashboards.

These are not tuned thresholds. They keep service identity explicit so the
evidence converters never have to guess a missing `service_name` label.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryPreset:
    name: str
    backend: str
    query: str
    service: str
    description: str


PRESETS: dict[str, QueryPreset] = {
    preset.name: preset
    for preset in (
        QueryPreset(
            "checkout-error-ratio",
            "prometheus",
            'sum(rate(http_server_request_duration_seconds_count{service_name="checkout",http_response_status_code=~"5.."}[5m])) / clamp_min(sum(rate(http_server_request_duration_seconds_count{service_name="checkout"}[5m])), 0.001)',
            "checkout",
            "Checkout 5xx ratio over 5 minutes",
        ),
        QueryPreset(
            "checkout-p95",
            "prometheus",
            'histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="checkout"}[5m])))',
            "checkout",
            "Checkout HTTP p95 latency in seconds",
        ),
        QueryPreset(
            "payment-error-spans",
            "prometheus",
            'sum(rate(traces_span_metrics_calls_total{service_name="payment",status_code="STATUS_CODE_ERROR"}[5m]))',
            "payment",
            "Payment server-side error spans per second",
        ),
        QueryPreset(
            "shipping-p95",
            "prometheus",
            'histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="shipping"}[5m])))',
            "shipping",
            "Shipping HTTP p95 latency in seconds",
        ),
        QueryPreset(
            "recommendation-p95",
            "prometheus",
            'histogram_quantile(0.95, sum by (le, service_name) (rate(traces_span_metrics_duration_milliseconds_bucket{service_name="recommendation"}[5m])))',
            "recommendation",
            "Recommendation span p95 latency in milliseconds",
        ),
        QueryPreset(
            "product-catalog-p95",
            "prometheus",
            'histogram_quantile(0.95, sum by (le, service_name) (rate(http_server_request_duration_seconds_bucket{service_name="product-catalog"}[5m])))',
            "product-catalog",
            "Product catalog HTTP p95 latency in seconds",
        ),
        QueryPreset(
            "checkout-error-logs",
            "loki",
            '{service_name="checkout"} |= "error"',
            "checkout",
            "Recent checkout log lines containing error",
        ),
        QueryPreset(
            "payment-error-logs",
            "loki",
            '{service_name="payment"} |= "error"',
            "payment",
            "Recent payment log lines containing error",
        ),
        QueryPreset(
            "recommendation-error-logs",
            "loki",
            '{service_name="recommendation"} |= "error"',
            "recommendation",
            "Recent recommendation log lines containing error",
        ),
    )
}


def presets_by_backend(backend: str) -> list[QueryPreset]:
    return [preset for preset in PRESETS.values() if preset.backend == backend]
