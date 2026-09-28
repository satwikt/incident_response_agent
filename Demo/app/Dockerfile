FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user; keep the SQLite file on a writable path outside the code.
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data
ENV DB_PATH=sqlite:////data/todos.db
COPY --chown=app:app . .
USER app

# One worker on purpose: fault state and the demo scenario live in process memory.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
