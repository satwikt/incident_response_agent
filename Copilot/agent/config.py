"""Centralised runtime configuration for the SRE Copilot.

All values are read from environment variables (loaded via .env).
Import this module instead of calling os.getenv() directly in tools or agents.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Target application ────────────────────────────────────────────────────────
# Must match the `service.name` resource attribute set in the Demo app's OTel setup.
SERVICE_NAME: str = os.getenv("APP_SERVICE_NAME", "app")

# ── Observability endpoints ───────────────────────────────────────────────────
PROMETHEUS_URL: str = os.getenv("PROMETHEUS_URL", "http://host.docker.internal:9090")
LOKI_URL: str = os.getenv("LOKI_URL", "http://host.docker.internal:3100")
TEMPO_URL: str = os.getenv("TEMPO_URL", "http://host.docker.internal:3200")

# ── Application endpoints ─────────────────────────────────────────────────────
APP_HEALTH_URL: str = os.getenv("APP_HEALTH_URL", "http://host.docker.internal:8000/health")
APP_ADMIN_URL: str = os.getenv("APP_ADMIN_URL", "http://host.docker.internal:8000/admin/faults")

# ── Notifications ─────────────────────────────────────────────────────────────
# Discord incoming webhook URL. Leave blank to disable Discord notifications.
DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Reference docs / RAG ─────────────────────────────────────────────────────
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
REFERENCE_DOCS_PATH: Path = Path(
    os.getenv("REFERENCE_DOCS_PATH", str(_PROJECT_ROOT / "data" / "reference_docs"))
)
