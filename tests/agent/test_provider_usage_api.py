"""Hetzner provider readings must never fall back to the desktop GPU API."""

from agent import ai_usage, usage_api, provider_usage_api
import pytest


@pytest.fixture(autouse=True)
def clean_caches():
    ai_usage.clear_cache()
    provider_usage_api.clear_cache()
    yield
    ai_usage.clear_cache()
    provider_usage_api.clear_cache()


def test_provider_cache_shares_snapshot_and_retains_last_good(monkeypatch):
    calls = []
    payload = {"source": "hetzner-direct", "current": {"muse": {"remaining": 96}}}

    def get_json(url, **kwargs):
        calls.append(url)
        return payload

    monkeypatch.setattr(usage_api, "_get_json", get_json)
    monkeypatch.setattr(provider_usage_api.time, "monotonic", lambda: 1000.0)
    assert provider_usage_api.fetch_provider_usage() == payload["current"]
    assert provider_usage_api.fetch_provider_usage() == payload["current"]
    assert len(calls) == 1
    monkeypatch.setattr(provider_usage_api.time, "monotonic", lambda: 1601.0)
    assert provider_usage_api.fetch_provider_usage() == payload["current"]
    assert len(calls) == 2
    monkeypatch.setattr(usage_api, "_get_json", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline")))
    assert provider_usage_api.fetch_provider_usage(use_cache=False) == payload["current"]
    provider_usage_api.clear_cache()
    assert provider_usage_api.fetch_provider_usage(use_cache=False) is None


@pytest.mark.parametrize("payload", [{}, {"source": "desktop", "current": {}},
                                      {"source": "hetzner-direct", "current": []},
                                      {"source": "hetzner-direct", "points": [{"museRemaining": 96}]}])
def test_malformed_or_legacy_payload_is_not_a_current_reading(monkeypatch, payload):
    monkeypatch.setattr(usage_api, "_get_json", lambda *a, **kw: payload)
    assert provider_usage_api.fetch_provider_usage(use_cache=False) is None


def test_explicit_offline_snapshot_replaces_previous_success(monkeypatch):
    payload = {"source": "hetzner-direct", "current": {"muse": {"online": True, "remaining": 96}}}
    monkeypatch.setattr(usage_api, "_get_json", lambda *a, **kw: payload)
    assert ai_usage.read_footer_usage(use_cache=False)["muse_label"] == "96%"
    payload["current"] = {"muse": {"online": False, "remaining": 96}}
    assert ai_usage.read_footer_usage(use_cache=False)["muse_label"] == ""


def test_chatgpt_uses_hetzner_current_snapshot(monkeypatch):
    requested = []

    def get_json(url, **kwargs):
        requested.append(url)
        return {"source": "hetzner-direct", "schemaVersion": 2,
                "current": {"chatgpt": {"online": True, "remaining": 80}},
                "points": [{"chatgptRemaining": 12}]}

    monkeypatch.setattr(usage_api, "_get_json", get_json)
    monkeypatch.setenv("USAGE_API_URL", "http://desktop.invalid:8769")
    ai_usage.clear_cache()
    status = ai_usage.read_chatgpt_usage(use_cache=False)
    assert status == ai_usage.UsageStatus(available=True, remaining=80)
    assert requested == ["http://hermes-1.tailed34e0.ts.net:8770/provider-history.json"]
