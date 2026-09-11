"""Client for the local Usage API (GPU, ChatGPT, Grok, OpenRouter).

The Windows tray app (`usage-api`) owns the live readings. Hermes asks it over
Tailscale Magic DNS and does not scrape nvidia-smi / ChatGPT / Grok / OpenRouter
itself. Missing or offline sections stay empty so the footer can hide them.

Default: ``http://winpc-2.tailed34e0.ts.net:8769``. Override with ``USAGE_API_URL``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional

DEFAULT_URL = "http://winpc-2.tailed34e0.ts.net:8769"
_CACHE_TTL_SECONDS = 2.0
_cache: Optional[tuple[float, Optional[dict]]] = None
_TIMEOUT_SECONDS = 1.5


def usage_api_url() -> str:
    return (os.environ.get("USAGE_API_URL") or DEFAULT_URL).strip().rstrip("/")


def usage_api_urls() -> list[str]:
    url = usage_api_url()
    return [url] if url else []


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Usage API returned a non-object")
    return payload


def fetch_usage(use_cache: bool = True) -> Optional[dict]:
    """``GET /api/usage`` — GPU + ChatGPT + Grok + OpenRouter, or None."""
    global _cache
    now = time.monotonic()
    if use_cache and _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    payload = None
    base = usage_api_url()
    if base:
        try:
            payload = _get_json(f"{base}/api/usage")
        except Exception:
            payload = None
    _cache = (now, payload)
    return payload


def clear_cache() -> None:
    global _cache
    _cache = None
