"""Unit tests for agent.gpu (status-bar VRAM read-out)."""

import sys
import types
from unittest.mock import patch

from agent.gpu import (
    GpuStatus,
    clear_cache,
    format_gpu,
    gpu_category,
    read_gpu,
)


def _make_status(**over):
    base = dict(available=True, used_mib=19442, total_mib=32607,
                name="NVIDIA GeForce RTX 5090")
    base.update(over)
    return GpuStatus(**base)


def setup_function(_fn):
    clear_cache()


def test_format_gpu_gib_one_decimal():
    assert format_gpu(_make_status()) == "GPU 19.0/31.8G"


def test_format_gpu_empty_when_unavailable():
    assert format_gpu(GpuStatus(available=False)) == ""
    assert format_gpu(_make_status(used_mib=None)) == ""


def test_gpu_category_hot_first():
    assert gpu_category(_make_status(used_mib=960, total_mib=1000)) == "critical"
    assert gpu_category(_make_status(used_mib=850, total_mib=1000)) == "bad"
    assert gpu_category(_make_status(used_mib=600, total_mib=1000)) == "warn"
    assert gpu_category(_make_status(used_mib=100, total_mib=1000)) == "good"
    assert gpu_category(GpuStatus(available=False)) == "dim"


def test_read_gpu_parses_first_gpu():
    import agent.gpu as gpu_mod

    class _Out:
        returncode = 0
        stdout = "19442, 32607, NVIDIA GeForce RTX 5090\n100, 200, Other\n"

    with patch.object(gpu_mod, "_nvidia_smi_path", return_value="/usr/bin/nvidia-smi"), \
            patch("subprocess.run", return_value=_Out()):
        st = read_gpu(use_cache=False)
    assert st.available is True
    assert (st.used_mib, st.total_mib) == (19442, 32607)
    assert st.name == "NVIDIA GeForce RTX 5090"


def test_read_gpu_fails_open_without_smi():
    import agent.gpu as gpu_mod

    with patch.object(gpu_mod, "_nvidia_smi_path", return_value=None):
        assert read_gpu(use_cache=False).available is False


def test_read_gpu_caches():
    import agent.gpu as gpu_mod

    calls: list = []

    class _Out:
        returncode = 0
        stdout = "100, 1000, GPU\n"

    def fake_run(*a, **k):
        calls.append(1)
        return _Out()

    with patch.object(gpu_mod, "_nvidia_smi_path", return_value="/usr/bin/nvidia-smi"), \
            patch("subprocess.run", side_effect=fake_run):
        read_gpu(use_cache=True)
        read_gpu(use_cache=True)
    assert len(calls) == 1
