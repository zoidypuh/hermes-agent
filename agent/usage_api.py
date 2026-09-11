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
_GPU_CACHE_TTL_SECONDS = 30.0
_cache: Optional[tuple[float, Optional[dict]]] = None
_gpu_cache: Optional[tuple[float, Optional[dict]]] = None
_TIMEOUT_SECONDS = 5.0
_GPU_TIMEOUT_SECONDS = 8.0


def usage_api_url() -> str:
    return (os.environ.get("USAGE_API_URL") or DEFAULT_URL).strip().rstrip("/")


def usage_api_urls() -> list[str]:
    url = usage_api_url()
    return [url] if url else []


def _get_json(url: str, timeout: float = _TIMEOUT_SECONDS) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Usage API returned a non-object")
    return payload


def _fetch_path(
    path: str,
    cache_slot: Optional[tuple[float, Optional[dict]]],
    use_cache: bool,
    timeout: float,
    ttl: float = _CACHE_TTL_SECONDS,
) -> tuple[Optional[tuple[float, Optional[dict]]], Optional[dict]]:
    now = time.monotonic()
    if use_cache and cache_slot is not None and now - cache_slot[0] < ttl:
        return cache_slot, cache_slot[1]
    payload = None
    base = usage_api_url()
    if base:
        try:
            payload = _get_json(f"{base}{path}", timeout=timeout)
        except Exception:
            payload = None
    if payload is None and cache_slot is not None and cache_slot[1] is not None:
        return (now, cache_slot[1]), cache_slot[1]
    return (now, payload), payload


def fetch_usage(use_cache: bool = True) -> Optional[dict]:
    """``GET /api/usage`` — GPU + ChatGPT + Grok + OpenRouter, or None."""
    global _cache
    _cache, payload = _fetch_path("/api/usage", _cache, use_cache, _TIMEOUT_SECONDS)
    return payload


def fetch_gpu(use_cache: bool = True) -> Optional[dict]:
    """``GET /api/gpu`` — VRAM only. Does not wait on ChatGPT/Grok/OpenRouter."""
    global _gpu_cache
    _gpu_cache, payload = _fetch_path(
        "/api/gpu",
        _gpu_cache,
        use_cache,
        _GPU_TIMEOUT_SECONDS,
        ttl=_GPU_CACHE_TTL_SECONDS,
    )
    return payload


def clear_cache() -> None:
    global _cache, _gpu_cache
    _cache = None
    _gpu_cache = None
