"""Read Hetzner's sanitized provider snapshot, independently of desktop VRAM.

The collector owns authentication and provider refreshes. HTTP failures retain
its last good snapshot; explicit offline sections remain unavailable.
"""

from __future__ import annotations

import time
from typing import Optional

from agent import usage_api

PROVIDER_USAGE_URL = "http://hermes-1.tailed34e0.ts.net:8770/provider-history.json"
_CACHE_TTL_SECONDS = 600.0
_cache: Optional[tuple[float, dict]] = None


def fetch_provider_usage(use_cache: bool = True) -> Optional[dict]:
    """Return only the collector's current readings, never hourly history."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    try:
        payload = usage_api._get_json(PROVIDER_USAGE_URL, timeout=5.0)
        current = payload.get("current")
        if payload.get("source") != "hetzner-direct" or not isinstance(current, dict):
            raise ValueError("Missing Hetzner current provider snapshot")
    except Exception:
        current = _cache[1] if _cache is not None else None
    if current is not None:
        _cache = (now, current)
    return current


def clear_cache() -> None:
    """Drop the last provider snapshot (independent of the GPU cache)."""
    global _cache
    _cache = None
