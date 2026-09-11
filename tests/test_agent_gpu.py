"""Unit tests for agent.gpu (status-bar VRAM read-out)."""

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
    assert format_gpu(_make_status()) == "19.0/31.8G"


def test_format_gpu_empty_when_unavailable():
    assert format_gpu(GpuStatus(available=False)) == ""
    assert format_gpu(_make_status(used_mib=None)) == ""


def test_gpu_category_hot_first():
    assert gpu_category(_make_status(used_mib=960, total_mib=1000)) == "critical"
    assert gpu_category(_make_status(used_mib=850, total_mib=1000)) == "bad"
    assert gpu_category(_make_status(used_mib=600, total_mib=1000)) == "warn"
    assert gpu_category(_make_status(used_mib=100, total_mib=1000)) == "good"
    assert gpu_category(GpuStatus(available=False)) == "dim"


def test_read_gpu_from_usage_api():
    payload = {"gpu": {"online": True, "gpuUsedGb": 19.0, "gpuTotalGb": 31.84375}}
    with patch("agent.usage_api.fetch_usage", return_value=payload):
        st = read_gpu(use_cache=False)
    assert st.available is True
    assert (st.used_mib, st.total_mib) == (19456, 32608)


def test_read_gpu_hides_when_api_offline():
    with patch("agent.usage_api.fetch_usage", return_value=None):
        assert read_gpu(use_cache=False).available is False
    with patch("agent.usage_api.fetch_usage", return_value={"gpu": {"online": False}}):
        assert format_gpu(read_gpu(use_cache=False)) == ""


def test_read_gpu_caches():
    calls = []

    def fake_fetch(*_a, **_k):
        calls.append(1)
        return {"gpu": {"online": True, "gpuUsedGb": 1.0, "gpuTotalGb": 32.0}}

    with patch("agent.usage_api.fetch_usage", side_effect=fake_fetch):
        read_gpu(use_cache=True)
        read_gpu(use_cache=True)
    assert len(calls) == 1
