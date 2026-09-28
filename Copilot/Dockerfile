FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Unprivileged user; /app/data holds the SQLite file and is a mounted volume.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown -R app:app /app
COPY --chown=app:app . .
USER app

EXPOSE 8001

# One worker on purpose: the watcher and the in-process rate limiter assume a single process.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
