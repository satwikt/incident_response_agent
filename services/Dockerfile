FROM python:3.12-slim

WORKDIR /app

ENV UV_HTTP_TIMEOUT=120
ENV UV_HTTP_RETRIES=4

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system -r requirements.txt

COPY . /app

ENV PYTHONPATH=/app

CMD ["uvicorn", "frontend.main:app", "--host", "0.0.0.0", "--port", "8080"]

