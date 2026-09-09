"""GPU VRAM read-out for the CLI/TUI status bar.

Queries the first NVIDIA GPU through ``nvidia-smi`` and exposes a compact,
colour-coded label (``GPU 18.8/32.0G``). Everything degrades to "unavailable"
(no GPU / read failure) so callers can render unconditionally. Like
:mod:`agent.battery`, :func:`read_gpu` memoises the reading — the status bar
repaints constantly, and a subprocess per repaint would be wasteful.

Only discrete NVIDIA GPUs via ``nvidia-smi`` are supported (the path lookup
covers PATH, the WSL ``/usr/lib/wsl/lib`` shim, and the Windows driver dirs,
mirroring ``hermes_cli.local_runtime.hardware``). Multi-GPU hosts report
device 0 only.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class GpuStatus:
    """One reading: MiB values straight from ``nvidia-smi``; ``name`` is informational."""

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

# The driver doesn't move mid-process; the PATH lookup happens once.
_smi_path_cache: "tuple[str | None] | None" = None


def _nvidia_smi_path() -> Optional[str]:
    """Absolute path to nvidia-smi, or None. PATH first, then known install spots."""
    global _smi_path_cache
    if _smi_path_cache is not None:
        return _smi_path_cache[0]
    found = shutil.which("nvidia-smi")
    if found is None and os.name == "nt":
        windir = os.environ.get("SystemRoot", r"C:\Windows")
        candidates = (
            Path(windir) / "System32" / "nvidia-smi.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
        )
        found = next((str(c) for c in candidates if c.exists()), None)
    elif found is None and sys.platform.startswith("linux"):
        candidate = Path("/usr/lib/wsl/lib/nvidia-smi")
        if candidate.exists():
            found = str(candidate)
    _smi_path_cache = (found,)
    return found


def _read_gpu_uncached() -> GpuStatus:
    exe = _nvidia_smi_path()
    if exe is None:
        return UNAVAILABLE
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return UNAVAILABLE
    if out.returncode != 0 or not (out.stdout or "").strip():
        return UNAVAILABLE
    try:
        first = out.stdout.strip().splitlines()[0]
        used_s, total_s, name = [p.strip() for p in first.split(",", 2)]
        used, total = int(float(used_s)), int(float(total_s))
        if total <= 0 or used < 0:
            return UNAVAILABLE
        return GpuStatus(available=True, used_mib=used, total_mib=total,
                         name=name or None)
    except (ValueError, IndexError):
        return UNAVAILABLE


def read_gpu(use_cache: bool = True) -> GpuStatus:
    """Return the current GPU VRAM status (cached for a couple of seconds)."""
    global _cache
    if use_cache and _cache is not None and time.monotonic() - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    status = _read_gpu_uncached()
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
