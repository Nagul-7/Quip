import json
from typing import Any, Optional
from loguru import logger
import redis.asyncio as aioredis

from config import get_settings

_redis_pool: Optional[aioredis.Redis] = None


async def get_redis() -> Optional[aioredis.Redis]:
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        try:
            _redis_pool = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
            )
            await _redis_pool.ping()
            logger.info("Redis connected at {}", settings.redis_url)
        except Exception as exc:
            logger.warning("Redis unavailable — caching disabled. Reason: {}", exc)
            _redis_pool = None
    return _redis_pool


async def cache_get(key: str) -> Optional[Any]:
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Cache GET failed for key={}: {}", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    r = await get_redis()
    if r is None:
        return
    settings = get_settings()
    try:
        await r.set(key, json.dumps(value, default=str), ex=ttl or settings.cache_ttl)
    except Exception as exc:
        logger.warning("Cache SET failed for key={}: {}", key, exc)


async def cache_delete(key: str) -> None:
    r = await get_redis()
    if r is None:
        return
    try:
        await r.delete(key)
    except Exception as exc:
        logger.warning("Cache DELETE failed for key={}: {}", key, exc)


async def cache_exists(key: str) -> bool:
    r = await get_redis()
    if r is None:
        return False
    try:
        return bool(await r.exists(key))
    except Exception:
        return False
