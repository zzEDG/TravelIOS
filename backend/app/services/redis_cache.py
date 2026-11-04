"""Optional Redis cache for successful third-party responses."""
import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

def cache_key(provider, operation, params, scope=""):
    payload = json.dumps({"params": params, "scope": scope}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"travelos:cache:v1:{provider}:{operation}:{digest}"

class RedisCache:
    def __init__(self, client):
        self.client = client

    async def get(self, key):
        try:
            raw = await self.client.get(key)
            if raw is None:
                return None
            value = json.loads(raw)
            return value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else None
        except Exception as exc:
            logger.warning("Cache read failed: %s", type(exc).__name__)
            return None

    async def set(self, key, data, ttl):
        try:
            value = {"cached_at": datetime.now(timezone.utc).isoformat(), "data": data}
            await self.client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception as exc:
            logger.warning("Cache write failed: %s", type(exc).__name__)

    async def close(self):
        await self.client.aclose()

def create_cache(url):
    if not url:
        return None
    from redis.asyncio import Redis
    return RedisCache(Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2, retry_on_timeout=False))
