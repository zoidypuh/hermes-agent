"""Remaining-usage read-outs for the CLI/TUI status bar (ChatGPT weekly + Grok).

Reads the user's own OAuth tokens from ``~/.cli-proxy-api/`` (the same files
the local CLI proxy uses) and calls the ChatGPT / Grok backends directly —
no proxy process required, so the footer works even when the monitor tray app
is asleep or the Mac is offline.

Everything degrades to "unavailable" (no token / network failure) so callers
can render unconditionally. Like :mod:`agent.battery` and :mod:`agent.gpu`,
reads are memoised — the status bar repaints constantly, and HTTPS per
repaint would be wasteful.

ChatGPT: ``GET https://chatgpt.com/backend-api/wham/usage`` with the
``codex-*.json`` access token + ``ChatGPT-Account-Id`` header. The weekly
window is the ``rate_limit`` window with ``limit_window_seconds >= 7d``
(``primary_window`` on current backends). Label = *remaining* percent.

Grok: ``GET https://cli-chat-proxy.grok.com/v1/billing?format=credits``
with the ``xai-*.json`` access token. Uses ``config.creditUsagePercent``
(used), falling back to ``used/monthlyLimit``. Label = *remaining* percent.
"""

from __future__ import annotations

import glob
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class UsageStatus:
    """One reading: remaining percent 0-100; unavailable when the token or
    network is missing."""

    available: bool
    remaining: Optional[int] = None


UNAVAILABLE = UsageStatus(available=False)

CHATGPT_UNAVAILABLE = UNAVAILABLE
GROK_UNAVAILABLE = UNAVAILABLE

# Colour buckets: a full remaining quota is "good", an empty one "critical".
CATEGORY_GOOD = "good"
CATEGORY_WARN = "warn"
CATEGORY_BAD = "bad"
CATEGORY_CRITICAL = "critical"
CATEGORY_DIM = "dim"

# (upper bound inclusive, category) for a *remaining* quota; first match wins.
_LEVEL_CATEGORIES = ((10, CATEGORY_CRITICAL), (20, CATEGORY_BAD), (50, CATEGORY_WARN))

_CACHE_TTL_SECONDS = 600.0
_cache: dict[str, tuple[float, UsageStatus]] = {}

_WEEKLY_WINDOW_SECONDS = 7 * 24 * 3600

_CHATGPT_URL = "https://chatgpt.com/backend-api/wham/usage"
_CHATGPT_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
_GROK_URLS = (
    "https://cli-chat-proxy.grok.com/v1/billing?format=credits",
    "https://cli-chat-proxy.grok.com/v1/billing",
)

_AUTH_DIR_CANDIDATES = ("~/.cli-proxy-api",)


def _clamp_percent(value) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(100, int(round(number))))


def _auth_dirs() -> list[Path]:
    dirs: list[Path] = []
    for candidate in _AUTH_DIR_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_dir() and path not in dirs:
            dirs.append(path)
    return dirs


def _read_token(pattern: str) -> tuple[Optional[str], Optional[dict]]:
    """First usable (not disabled/expired) auth file matching *pattern*."""
    for auth_dir in _auth_dirs():
        for path in sorted(auth_dir.glob(pattern)):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict) or payload.get("disabled") is True:
                continue
            token = payload.get("access_token")
            if not isinstance(token, str) or not token.strip():
                continue
            return token.strip(), payload
    return None, None


def _get_json(url: str, headers: dict, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _chatgpt_remaining_uncached() -> UsageStatus:
    token, payload = _read_token("codex-*.json")
    if token is None:
        return CHATGPT_UNAVAILABLE
    account_id = (payload or {}).get("account_id") or "b83a38ff-aaaa-4847-99a4-8d4413e10b73"
    try:
        usage = _get_json(
            _CHATGPT_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": _CHATGPT_UA,
                "ChatGPT-Account-Id": account_id,
            },
        )
    except Exception:
        return CHATGPT_UNAVAILABLE
    try:
        rate_limit = usage.get("rate_limit") or {}
        windows = [
            rate_limit.get(key)
            for key in ("primary_window", "secondary_window")
            if isinstance(rate_limit.get(key), dict)
            and rate_limit.get(key, {}).get("used_percent") is not None
        ]
        weekly = next(
            (
                w
                for w in windows
                if int(w.get("limit_window_seconds") or 0) >= _WEEKLY_WINDOW_SECONDS - 60
            ),
            windows[0] if windows else None,
        )
        used = _clamp_percent((weekly or {}).get("used_percent"))
        if used is None:
            return CHATGPT_UNAVAILABLE
        return UsageStatus(available=True, remaining=100 - used)
    except Exception:
        return CHATGPT_UNAVAILABLE


def _grok_remaining_uncached() -> UsageStatus:
    token, _payload = _read_token("xai-*.json")
    if token is None:
        return GROK_UNAVAILABLE
    usage: Optional[dict] = None
    for url in _GROK_URLS:
        try:
            usage = _get_json(url, headers={"Authorization": f"Bearer {token}"})
            break
        except Exception:
            continue
    if not isinstance(usage, dict):
        return GROK_UNAVAILABLE
    try:
        config = usage.get("config") or {}

        def _num(value):
            if isinstance(value, dict) and "val" in value:
                value = value.get("val")
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        credit_used = _num(config.get("creditUsagePercent"))
        if credit_used is not None:
            used = _clamp_percent(credit_used)
            if used is None:
                return GROK_UNAVAILABLE
            return UsageStatus(available=True, remaining=100 - used)
        used_units = _num(config.get("used"))
        limit_units = _num(config.get("monthlyLimit"))
        if used_units is None or not limit_units:
            return GROK_UNAVAILABLE
        used = _clamp_percent(used_units / limit_units * 100)
        if used is None:
            return GROK_UNAVAILABLE
        return UsageStatus(available=True, remaining=100 - used)
    except Exception:
        return GROK_UNAVAILABLE


def _cached(key: str, loader) -> UsageStatus:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]
    status = loader()
    _cache[key] = (now, status)
    return status


def read_chatgpt_usage(use_cache: bool = True) -> UsageStatus:
    """Remaining ChatGPT weekly quota (cached ~10min)."""
    if use_cache:
        return _cached("chatgpt", _chatgpt_remaining_uncached)
    return _chatgpt_remaining_uncached()


def read_grok_usage(use_cache: bool = True) -> UsageStatus:
    """Remaining Grok quota (cached ~10min)."""
    if use_cache:
        return _cached("grok", _grok_remaining_uncached)
    return _grok_remaining_uncached()


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
