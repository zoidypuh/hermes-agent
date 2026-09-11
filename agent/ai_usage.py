"""Remaining-usage read-outs for the CLI/TUI status bar (ChatGPT, Grok, OpenRouter).

Reads the local Usage API over Tailscale Magic DNS
(``http://winpc-2.tailed34e0.ts.net:8769/api/usage``). Missing / offline
sections stay unavailable so the footer can hide them.

OpenRouter remaining credits render as ``8,49$``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class UsageStatus:
    """One reading: remaining percent 0-100; unavailable when the API has no data."""

    available: bool
    remaining: Optional[int] = None


@dataclass(frozen=True)
class CreditsStatus:
    """One OpenRouter credits reading; unavailable when the API has no data."""

    available: bool
    remaining: Optional[float] = None


UNAVAILABLE = UsageStatus(available=False)

CHATGPT_UNAVAILABLE = UNAVAILABLE
GROK_UNAVAILABLE = UNAVAILABLE
OPENROUTER_UNAVAILABLE = CreditsStatus(available=False)

# Colour buckets: a full remaining quota is "good", an empty one "critical".
CATEGORY_GOOD = "good"
CATEGORY_WARN = "warn"
CATEGORY_BAD = "bad"
CATEGORY_CRITICAL = "critical"
CATEGORY_DIM = "dim"

# (upper bound inclusive, category) for a *remaining* quota; first match wins.
_LEVEL_CATEGORIES = ((10, CATEGORY_CRITICAL), (20, CATEGORY_BAD), (50, CATEGORY_WARN))
# Dollar remaining for OpenRouter credits; first match wins.
_CREDIT_CATEGORIES = ((1.0, CATEGORY_CRITICAL), (5.0, CATEGORY_BAD), (20.0, CATEGORY_WARN))

_CACHE_TTL_SECONDS = 600.0
_cache: dict[str, tuple[float, object]] = {}


def _clamp_percent(value) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, int(round(number))))


def _usage_api_section(name: str, use_cache: bool = True) -> Optional[dict]:
    try:
        from agent.usage_api import fetch_usage
        payload = fetch_usage(use_cache=use_cache)
    except Exception:
        return None
    section = (payload or {}).get(name) if isinstance(payload, dict) else None
    return section if isinstance(section, dict) else None


def _chatgpt_from_usage_api(use_cache: bool = True) -> UsageStatus:
    section = _usage_api_section("chatgpt", use_cache=use_cache)
    if not section or section.get("online") is False:
        return CHATGPT_UNAVAILABLE
    remaining = section.get("remaining")
    if remaining is None:
        remaining = section.get("weeklyRemaining")
    remaining = _clamp_percent(remaining)
    if remaining is None:
        used = _clamp_percent(section.get("used") or section.get("weeklyUsed"))
        if used is None:
            return CHATGPT_UNAVAILABLE
        remaining = 100 - used
    return UsageStatus(available=True, remaining=remaining)


def _grok_from_usage_api(use_cache: bool = True) -> UsageStatus:
    section = _usage_api_section("grok", use_cache=use_cache)
    if not section or section.get("online") is False:
        return GROK_UNAVAILABLE
    remaining = _clamp_percent(section.get("remaining"))
    if remaining is None:
        used = _clamp_percent(section.get("used"))
        if used is None:
            return GROK_UNAVAILABLE
        remaining = 100 - used
    return UsageStatus(available=True, remaining=remaining)


def _openrouter_from_usage_api(use_cache: bool = True) -> CreditsStatus:
    section = _usage_api_section("openrouter", use_cache=use_cache)
    if not section or section.get("online") is False:
        return OPENROUTER_UNAVAILABLE
    remaining = section.get("remainingCredits")
    if remaining is None:
        remaining = section.get("remaining")
        # Remaining percent is not dollars — only accept it if remainingCredits
        # was missing *and* remaining looks like a credit balance, not 0-100%.
        if remaining is not None:
            try:
                value = float(remaining)
            except (TypeError, ValueError):
                return OPENROUTER_UNAVAILABLE
            if 0 <= value <= 100 and section.get("used") is not None:
                return OPENROUTER_UNAVAILABLE
    try:
        value = float(remaining)
    except (TypeError, ValueError):
        return OPENROUTER_UNAVAILABLE
    return CreditsStatus(available=True, remaining=max(0.0, value))


def _cached(key: str, loader):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    status = loader()
    _cache[key] = (now, status)
    return status


def read_chatgpt_usage(use_cache: bool = True) -> UsageStatus:
    """Remaining ChatGPT weekly quota from the Usage API (cached ~10min)."""
    if use_cache:
        return _cached("chatgpt", lambda: _chatgpt_from_usage_api(use_cache=True))
    return _chatgpt_from_usage_api(use_cache=False)


def read_grok_usage(use_cache: bool = True) -> UsageStatus:
    """Remaining Grok quota from the Usage API (cached ~10min)."""
    if use_cache:
        return _cached("grok", lambda: _grok_from_usage_api(use_cache=True))
    return _grok_from_usage_api(use_cache=False)


def read_openrouter_credits(use_cache: bool = True) -> CreditsStatus:
    """Remaining OpenRouter credits from the Usage API (cached ~10min)."""
    if use_cache:
        return _cached("openrouter", lambda: _openrouter_from_usage_api(use_cache=True))
    return _openrouter_from_usage_api(use_cache=False)


def clear_cache() -> None:
    """Drop memoised readings (used by tests)."""
    _cache.clear()


def usage_category(status: UsageStatus) -> str:
    """Bucket a *remaining* quota: full = good, empty = critical."""
    if not status.available or status.remaining is None:
        return CATEGORY_DIM
    for bound, category in _LEVEL_CATEGORIES:
        if status.remaining <= bound:
            return category
    return CATEGORY_GOOD


def credits_category(status: CreditsStatus) -> str:
    """Bucket remaining OpenRouter dollars: plenty = good, empty = critical."""
    if not status.available or status.remaining is None:
        return CATEGORY_DIM
    for bound, category in _CREDIT_CATEGORIES:
        if status.remaining <= bound:
            return category
    return CATEGORY_GOOD


def format_chatgpt(status: UsageStatus) -> str:
    """Compact label like ``76%`` (empty if N/A) — no name prefix."""
    if not status.available or status.remaining is None:
        return ""
    return f"{status.remaining}%"


def format_grok(status: UsageStatus) -> str:
    """Compact label like ``51%`` (empty if N/A) — no name prefix."""
    if not status.available or status.remaining is None:
        return ""
    return f"{status.remaining}%"


def format_openrouter(status: CreditsStatus) -> str:
    """Compact remaining-credits label like ``8,49$`` (empty if N/A)."""
    if not status.available or status.remaining is None:
        return ""
    euros = f"{status.remaining:.2f}".replace(".", ",")
    return f"{euros}$"
