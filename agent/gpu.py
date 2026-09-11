"""GPU VRAM read-out for the CLI/TUI status bar.

Reads the local Usage API over Tailscale Magic DNS
(``http://winpc-2.tailed34e0.ts.net:8769/api/usage``). Missing / offline
readings stay unavailable so the footer can hide the segment.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GpuStatus:
    """One reading: MiB values from the Usage API; ``name`` is informational."""

    available: bool
    used_mib: Optional[int] = None
    total_mib: Optional[int] = None
    name: Optional[str] = None

    @property
    def used_pct(self) -> Optional[float]:
        if self.used_mib is None or self.total_mib is None or self.total_mib <= 0:
            return None
        return max(0.0, min(100.0, (self.used_mib / self.total_mib) * 100))


UNAVAILABLE = GpuStatus(available=False)

# Colour buckets mirroring the status-bar context styles: a full card is
# "critical", an idle one "good" (opposite of the battery mapping).
CATEGORY_GOOD = "good"
CATEGORY_WARN = "warn"
CATEGORY_BAD = "bad"
CATEGORY_CRITICAL = "critical"
CATEGORY_DIM = "dim"

# (lower bound inclusive, category); first match wins, evaluated hot-first.
_LEVEL_CATEGORIES = ((95, CATEGORY_CRITICAL), (80, CATEGORY_BAD), (50, CATEGORY_WARN))

_CACHE_TTL_SECONDS = 2.0
_cache: Optional[tuple[float, GpuStatus]] = None


def _read_gpu_uncached(use_cache: bool = True) -> GpuStatus:
    try:
        from agent.usage_api import fetch_usage
        payload = fetch_usage(use_cache=use_cache)
    except Exception:
        return UNAVAILABLE
    gpu = (payload or {}).get("gpu") if isinstance(payload, dict) else None
    if not isinstance(gpu, dict) or gpu.get("online") is False:
        return UNAVAILABLE
    used_gb = gpu.get("gpuUsedGb")
    total_gb = gpu.get("gpuTotalGb")
    try:
        used_mib = int(round(float(used_gb) * 1024))
        total_mib = int(round(float(total_gb) * 1024))
    except (TypeError, ValueError):
        return UNAVAILABLE
    if total_mib <= 0 or used_mib < 0:
        return UNAVAILABLE
    return GpuStatus(available=True, used_mib=used_mib, total_mib=total_mib, name="GPU")


def read_gpu(use_cache: bool = True) -> GpuStatus:
    """Return the current GPU VRAM status (cached for a couple of seconds)."""
    global _cache
    if use_cache and _cache is not None and time.monotonic() - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    status = _read_gpu_uncached(use_cache=use_cache)
    _cache = (time.monotonic(), status)
    return status


def clear_cache() -> None:
    """Drop the memoised reading (used by tests)."""
    global _cache
    _cache = None


def gpu_category(status: GpuStatus) -> str:
    """Bucket a reading into a colour category: good/warn/bad/critical/dim."""
    pct = status.used_pct if status.available else None
    if pct is None:
        return CATEGORY_DIM
    for bound, category in _LEVEL_CATEGORIES:
        if pct >= bound:
            return category
    return CATEGORY_GOOD


def format_gpu(status: GpuStatus) -> str:
    """Compact label like ``19.0/31.8G`` (empty if N/A). No ``GPU`` prefix —
    the footer position makes the source obvious."""
    if not status.available or status.used_mib is None or status.total_mib is None:
        return ""
    return f"{status.used_mib / 1024:.1f}/{status.total_mib / 1024:.1f}G"
