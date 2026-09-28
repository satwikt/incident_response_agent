"""Redis client wrapper for caching session context."""

import logging
import os
import redis

log = logging.getLogger("copilot.redis")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

_raw = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

class SafeRedis:
    """Best-effort Redis cache wrapper; graceful fallback if Redis is unreachable."""

    def get(self, key: str):
        try:
            return _raw.get(key)
        except Exception as exc:
            log.warning("redis get failed: %s", exc)
            return None

    def set(self, key: str, value: str, ex: int = 3600):
        try:
            return _raw.set(key, value, ex=ex)
        except Exception as exc:
            log.warning("redis set failed: %s", exc)
            return None

    def delete(self, key: str):
        try:
            return _raw.delete(key)
        except Exception as exc:
            log.warning("redis delete failed: %s", exc)
            return 0


redis_client = SafeRedis()
