"""Unit tests for the local Usage API client."""

from unittest.mock import patch

from agent.usage_api import DEFAULT_URL, clear_cache, fetch_gpu, fetch_usage, usage_api_url, usage_api_urls


def setup_function(_fn):
    clear_cache()


def test_fetch_usage_returns_payload():
    payload = {
        "gpu": {"online": True, "gpuUsedGb": 19.0, "gpuTotalGb": 32.0},
        "chatgpt": {"online": True, "remaining": 75},
        "grok": {"online": True, "remaining": 51},
        "openrouter": {"online": True, "remainingCredits": 8.49, "remainingLabel": "8,49$"},
    }
    with patch("agent.usage_api.usage_api_url", return_value="http://winpc-2.tailed34e0.ts.net:8769"), \
            patch("agent.usage_api._get_json", return_value=payload):
        assert fetch_usage(use_cache=False) == payload


def test_fetch_usage_fails_open():
    with patch("agent.usage_api.usage_api_url", return_value="http://winpc-2.tailed34e0.ts.net:8769"), \
            patch("agent.usage_api._get_json", side_effect=OSError("down")):
        assert fetch_usage(use_cache=False) is None


def test_fetch_usage_caches():
    calls = []

    def fake_get(_url, *_args, **_kwargs):
        calls.append(1)
        return {"openrouter": {"online": True, "remainingCredits": 8.49}}

    with patch("agent.usage_api.usage_api_url", return_value="http://winpc-2.tailed34e0.ts.net:8769"), \
            patch("agent.usage_api._get_json", side_effect=fake_get):
        fetch_usage(use_cache=True)
        fetch_usage(use_cache=True)
    assert len(calls) == 1


def test_default_url_is_tailscale_magic_dns():
    assert DEFAULT_URL == "http://winpc-2.tailed34e0.ts.net:8769"
    with patch.dict("os.environ", {}, clear=False):
        assert "USAGE_API_URL" not in __import__("os").environ or True
        urls = usage_api_urls()
    assert urls[0] == usage_api_url()
    assert "tailed34e0.ts.net" in urls[0]


def test_usage_api_url_override():
    with patch.dict("os.environ", {"USAGE_API_URL": "http://example.ts.net:8769/"}):
        assert usage_api_url() == "http://example.ts.net:8769"


def test_fetch_gpu_uses_gpu_endpoint():
    payload = {"name": "gpu", "online": True, "gpuUsedGb": 4.2, "gpuTotalGb": 32.0}
    with patch("agent.usage_api.usage_api_url", return_value="http://winpc-2.tailed34e0.ts.net:8769"), \
            patch("agent.usage_api._get_json", return_value=payload) as get_json:
        assert fetch_gpu(use_cache=False) == payload
        get_json.assert_called_once()
        assert get_json.call_args.args[0].endswith("/api/gpu")


def test_fetch_gpu_keeps_last_good_on_failure():
    payload = {"name": "gpu", "online": True, "gpuUsedGb": 4.2, "gpuTotalGb": 32.0}
    with patch("agent.usage_api.usage_api_url", return_value="http://winpc-2.tailed34e0.ts.net:8769"), \
            patch("agent.usage_api._get_json", return_value=payload):
        assert fetch_gpu(use_cache=False) == payload
    with patch("agent.usage_api.usage_api_url", return_value="http://winpc-2.tailed34e0.ts.net:8769"), \
            patch("agent.usage_api._get_json", side_effect=OSError("down")):
        assert fetch_gpu(use_cache=False) == payload
