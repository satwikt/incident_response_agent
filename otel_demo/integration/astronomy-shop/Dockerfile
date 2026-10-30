FROM python:3.12-alpine

WORKDIR /app
COPY adapter/ /app/adapter/

ENV PYTHONUNBUFFERED=1
ENV PROMETHEUS_URL=http://prometheus:9090
ENV LOKI_URL=http://loki:3100
ENV TEMPO_URL=http://tempo:3200

ENTRYPOINT ["python3", "-m", "adapter"]
CMD ["--ingest-url", "http://copilot:8001/ingest/events", "--preset", "checkout-error-ratio", "--preset", "checkout-error-logs", "--tempo-service", "checkout", "--interval", "5"]
