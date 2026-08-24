import asyncio
import hashlib
import json
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from jose import jwk

from ..telemetry import get_logger

logger = get_logger(__name__)

# See memory/specs/002-jwt-authentication-middleware.md — AC-12
_CACHE_TTL: int = 300  # 5 minutes
# L1 in-process TTL — avoids Redis round-trip per request (AC-7 design decision)
_L1_CACHE_TTL: int = 30  # 30 seconds in-process; Redis is L2 at 5 min

# [HIGH fix] Minimum interval between forced cache invalidations (anti-DoS amplifier).
# An unauthenticated caller cannot trigger more than one IdP re-fetch per 60 s.
_FORCED_REFRESH_MIN_INTERVAL: float = 60.0

# [MEDIUM fix] Hard limits on JWKS response to prevent resource exhaustion.
_JWKS_MAX_RESPONSE_BYTES: int = 65_536  # 64 KB
_JWKS_MAX_KEY_COUNT: int = 10

_cache: dict[str, Any] = {}
_last_forced_refresh: float = 0.0

# asyncio.Lock is safe as a module-level constant in Python 3.11+
_lock = asyncio.Lock()

# Module-level Redis client — injected at startup for cross-worker JWKS caching (AC-7)
_redis_client: Any = None


def set_jwks_redis_client(client: Any) -> None:
    """Inject a Redis client for the L2 JWKS cache.

    Must be called during application startup before any requests are served.
    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-7
    """
    global _redis_client
    _redis_client = client


def _jwks_redis_key(jwks_url: str) -> str:
    """Return the Redis key for caching JWKS keys for the given URL."""
    url_hash = hashlib.sha256(jwks_url.encode()).hexdigest()[:16]
    return f"prometheus:jwks:{url_hash}"


def _safe_url_for_log(url: str) -> str:
    """Strip userinfo (credentials) from a URL before writing it to logs.

    [LOW fix] Prevents operator-embedded credentials in JWKS URLs leaking to log aggregators.
    """
    parsed = urlparse(url)
    safe_netloc = parsed.hostname or ""
    if parsed.port:
        safe_netloc = f"{safe_netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=safe_netloc))


async def fetch_jwks_keys(
    jwks_url: str, *, force_refresh: bool = False, tls_verify: bool = True
) -> list[Any]:
    """Fetch RS256 public keys from a JWKS endpoint with a two-level cache.

    L1: in-process dict with 30-second TTL (avoids per-request Redis hit).
    L2: Redis with 5-minute TTL, shared across uvicorn workers.

    Implements: memory/specs/002-jwt-authentication-middleware.md — AC-12
    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-7
    On signature failure the caller should call invalidate_jwks_cache() then
    retry once (retry-once pattern) to handle key rotation transparently.
    """
    async with _lock:
        now = time.monotonic()

        # L1 in-process cache check (AC-7: 30 s TTL)
        if not force_refresh and "fetched_at" in _cache:
            if (now - _cache["fetched_at"]) < _L1_CACHE_TTL:
                return _cache["keys"]  # type: ignore[no-any-return]

        # L2 Redis cache check (AC-7)
        if not force_refresh and _redis_client is not None:
            try:
                redis_data = await _redis_client.get(_jwks_redis_key(jwks_url))
                if redis_data:
                    raw_keys = json.loads(redis_data)
                    keys: list[Any] = [jwk.construct(k, algorithm="RS256") for k in raw_keys]
                    _cache["keys"] = keys
                    _cache["fetched_at"] = now
                    logger.debug("jwks.l2_cache_hit", url=_safe_url_for_log(jwks_url))
                    return keys
            except Exception as exc:
                # Redis unavailable — fall through to live fetch (AC-7: fail-safe)
                logger.warning("jwks.redis_cache_error", error=str(exc))

        logger.info("jwks.fetching", url=_safe_url_for_log(jwks_url))
        async with httpx.AsyncClient(verify=tls_verify) as client:
            resp = await client.get(jwks_url, timeout=5.0)
            resp.raise_for_status()

            # [MEDIUM fix] Guard against oversized JWKS responses.
            if len(resp.content) > _JWKS_MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"JWKS response size ({len(resp.content)} bytes) "
                    f"exceeds the {_JWKS_MAX_RESPONSE_BYTES}-byte limit."
                )
            data = resp.json()

        rsa_entries = [k for k in data.get("keys", []) if k.get("kty") == "RSA"]

        # [MEDIUM fix] Guard against unreasonably large key sets.
        if len(rsa_entries) > _JWKS_MAX_KEY_COUNT:
            raise ValueError(
                f"JWKS contains {len(rsa_entries)} RSA keys; "
                f"maximum allowed is {_JWKS_MAX_KEY_COUNT}."
            )

        keys = [jwk.construct(k, algorithm="RS256") for k in rsa_entries]

        _cache["keys"] = keys
        _cache["fetched_at"] = now

        # Populate Redis L2 cache (AC-7)
        if _redis_client is not None:
            try:
                await _redis_client.set(
                    _jwks_redis_key(jwks_url),
                    json.dumps(rsa_entries),
                    ex=_CACHE_TTL,
                )
            except Exception as exc:
                logger.warning("jwks.redis_cache_write_error", error=str(exc))

        logger.info("jwks.refreshed", key_count=len(keys))
        return keys


def invalidate_jwks_cache() -> bool:
    """Rate-limited JWKS cache flush — used after a signature failure (retry-once pattern).

    [HIGH fix] Returns True and clears the cache only if the last forced refresh was
    more than _FORCED_REFRESH_MIN_INTERVAL seconds ago, preventing unauthenticated
    callers from using the gateway as a DoS amplifier against the IdP.
    Returns False (no-op) if called within the minimum interval.
    """
    global _last_forced_refresh
    now = time.monotonic()
    if now - _last_forced_refresh < _FORCED_REFRESH_MIN_INTERVAL:
        return False  # throttled — skip invalidation
    _cache.clear()
    _last_forced_refresh = now
    return True


def _reset_cache_for_testing() -> None:
    """Reset all module-level cache state. For use in test fixtures only."""
    global _last_forced_refresh, _redis_client
    _cache.clear()
    _last_forced_refresh = 0.0
    _redis_client = None
