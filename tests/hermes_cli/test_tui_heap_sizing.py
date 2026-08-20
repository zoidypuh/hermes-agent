"""Tests for cgroup-aware TUI V8 heap sizing.

V8 is not cgroup-aware: a flat ``--max-old-space-size=8192`` lets the heap grow
toward 8GB in a memory-limited container, so the cgroup OOM-killer SIGKILLs Node
before V8's own monitor fires — leaving the user with only a bare gateway
``stdin EOF`` and no breadcrumb. ``_resolve_tui_heap_mb`` reads the real cgroup
limit and sizes the cap below it so V8 exits gracefully instead.
"""

import builtins
import io
from unittest import mock

import hermes_cli.main as m

V2 = "/sys/fs/cgroup/memory.max"
V1 = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
GB = 1024 ** 3


def _fake_open(files: dict):
    """Return an open() shim serving cgroup paths from ``files`` (path->str)."""
    real_open = builtins.open

    def opener(path, *args, **kwargs):
        if path in (V2, V1):
            content = files.get(path)
            if content is None:
                raise FileNotFoundError(path)
            return io.StringIO(content)
        return real_open(path, *args, **kwargs)

    return opener


def _read(files: dict):
    with mock.patch.object(builtins, "open", _fake_open(files)):
        return m._read_cgroup_memory_limit()


class TestReadCgroupMemoryLimit:
    def test_v2_max_is_unlimited(self):
        assert _read({V2: "max"}) is None


class TestResolveTuiHeapMb:
    def _resolve(self, limit_bytes):
        with mock.patch.object(m, "_read_cgroup_memory_limit", return_value=limit_bytes):
            return m._resolve_tui_heap_mb()

    def test_unconstrained_uses_default(self):
        assert self._resolve(None) == 8192


class TestNodeOptionsTokenMerge:
    """``_apply_tui_node_options`` adds the heap cap and strips flags Node
    rejects in NODE_OPTIONS (those go on argv via ``_tui_node_argv``)."""

    def _merge(self, node_options, limit_bytes):
        with mock.patch.object(m, "_read_cgroup_memory_limit", return_value=limit_bytes):
            env = {"NODE_OPTIONS": node_options}
            m._apply_tui_node_options(env)
            return env["NODE_OPTIONS"]

    def test_unconstrained_empty(self):
        assert self._merge("", None) == "--max-old-space-size=8192"

    def test_preserves_other_flags(self):
        assert (
            self._merge("--enable-source-maps", 4 * GB)
            == "--enable-source-maps --max-old-space-size=3072"
        )

    def test_strips_forbidden_v8_flags(self):
        assert (
            self._merge("--no-concurrent-recompilation --expose-gc", None)
            == "--max-old-space-size=8192"
        )


class TestTuiNodeArgv:
    def test_includes_turbofan_workaround(self):
        assert m._tui_node_argv("/usr/bin/node", "/tmp/entry.js") == [
            "/usr/bin/node",
            "--expose-gc",
            "--no-concurrent-recompilation",
            "/tmp/entry.js",
        ]
