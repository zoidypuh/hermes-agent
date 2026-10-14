"""End-to-end quota parsing and both classic footer render paths."""

from datetime import datetime

import pytest

from agent import provider_usage_api, ai_usage
from cli import HermesCLI


@pytest.fixture
def footer(monkeypatch):
    ai_usage.clear_cache()
    payload = {
        "chatgpt": {"online": True, "remaining": 80},
        "grok": {"online": True, "remaining": 99},  # Not an individual account.
        "grokAccounts": [
            {"accountId": "grok4@zoidbergcg.com", "online": True, "remaining": 0},
            {"accountId": "thegismar@gmail.com", "online": True, "remaining": 72},
            {"accountId": "grok3@zoidbergcg.com", "online": True, "remaining": 0},
        ],
        "muse": {"online": True, "remaining": 96},
        "openrouter": {"online": True, "remainingCredits": 9.259},
    }
    monkeypatch.setattr(provider_usage_api, "fetch_provider_usage", lambda **kw: payload)
    obj = HermesCLI.__new__(HermesCLI)
    obj.model = "test-model"
    obj.agent = None
    obj.session_start = datetime.now()
    obj.conversation_history = []
    obj._gpu_visible = False
    obj._ai_usage_visible = True
    obj._status_bar_visible = True
    obj._status_bar_field_set_cache = frozenset({"model", "gpu", "chatgpt", "grok", "muse", "openrouter"})
    monkeypatch.setattr(obj, "_read_status_bar_metric", lambda name, reader, ttl: reader())
    monkeypatch.setattr(obj, "_get_tui_terminal_width", lambda: 200)
    monkeypatch.setattr(obj, "_is_session_yolo_active", lambda: False)
    return obj, payload


def test_footer_exact_cluster_in_both_renderers(footer, monkeypatch):
    obj, _ = footer
    snapshot = obj._get_status_bar_snapshot()
    # GPU reader/formatter stays unchanged; the footer owns its lowercase unit.
    snapshot["gpu_label"] = "30.8G"
    monkeypatch.setattr(obj, "_get_status_bar_snapshot", lambda: snapshot)
    expected = "| 30.8g | 80% | 72%, 0%, 0% | 96% | $9.259"
    assert expected in obj._build_status_bar_text(200)
    assert expected in "".join(text for _, text in obj._get_status_bar_fragments())


def test_chatgpt_weekly_fallback_preserves_zero_usage(footer):
    obj, payload = footer
    payload["chatgpt"] = {"online": True, "weeklyRemaining": 84}
    assert obj._get_status_bar_snapshot()["chatgpt_label"] == "84%"
    payload["chatgpt"] = {"online": True, "weeklyUsed": 0}
    assert obj._get_status_bar_snapshot()["chatgpt_label"] == "100%"


def test_explicit_zero_usage_agrees_between_footer_and_legacy_reader(footer):
    obj, payload = footer
    payload["chatgpt"] = {"online": True, "used": 0, "weeklyUsed": 40}
    assert obj._get_status_bar_snapshot()["chatgpt_label"] == "100%"
    assert ai_usage.format_chatgpt(ai_usage.read_chatgpt_usage(use_cache=False)) == "100%"


def test_grok_slots_follow_identity_not_list_order_or_remaining(footer):
    from itertools import permutations

    obj, payload = footer
    values = {"thegismar@gmail.com": 12, "grok3@zoidbergcg.com": 34, "grok4@zoidbergcg.com": 56}
    accounts = [{"accountId": name, "online": True, "remaining": value} for name, value in values.items()]
    for order in permutations(accounts):
        payload["grokAccounts"] = list(order)
        assert obj._get_status_bar_snapshot()["grok_label"] == "12%, 34%, 56%"


def test_missing_account_keeps_slot_and_offline_does_not_become_zero(footer):
    obj, payload = footer
    payload["grokAccounts"] = [
        {"accountId": "grok4@zoidbergcg.com", "online": True, "remaining": 0},
        {"accountId": "thegismar@gmail.com", "online": False, "remaining": 72},
        {"accountId": "unknown", "online": True, "remaining": 100},
    ]
    assert obj._get_status_bar_snapshot()["grok_label"] == "n/a, n/a, 0%"
    payload["grokAccounts"] = []
    assert obj._get_status_bar_snapshot()["grok_label"] == ""


@pytest.mark.parametrize("raw,expected", [(72.6, "73%"), (-3, "0%"), (120, "100%"), ("41.2", "41%"),
                                          (float("nan"), ""), (float("inf"), ""), (None, "")])
def test_remaining_percent_formatting(footer, raw, expected):
    obj, payload = footer
    payload["muse"]["remaining"] = raw
    assert obj._get_status_bar_snapshot()["muse_label"] == expected


@pytest.mark.parametrize("raw,expected", [(9.2594, "$9.259"), (9.2599, "$9.260"), (0, "$0.000"),
                                          (-1, "$0.000"), (float("nan"), ""), (float("inf"), ""), (None, "")])
def test_dollar_formatting(footer, raw, expected):
    obj, payload = footer
    payload["openrouter"]["remainingCredits"] = raw
    assert obj._get_status_bar_snapshot()["openrouter_label"] == expected


def test_offline_providers_hide_and_master_switch_prevents_reads(footer, monkeypatch):
    obj, payload = footer
    for key in ("chatgpt", "muse", "openrouter"):
        payload[key]["online"] = False
        assert obj._get_status_bar_snapshot()[key + "_label"] == ""
    obj._ai_usage_visible = False
    monkeypatch.setattr(provider_usage_api, "fetch_provider_usage", lambda **kw: pytest.fail("usage disabled"))
    assert obj._get_status_bar_snapshot()["grok_label"] == ""


def test_legacy_quota_allowlist_includes_muse(footer, monkeypatch):
    import cli

    obj, _ = footer
    del obj._status_bar_field_set_cache
    monkeypatch.setattr(cli, "CLI_CONFIG", {"display": {"status_bar": {"fields": ["model", "chatgpt", "grok"]}}})
    assert {"muse", "openrouter"} <= obj._get_status_bar_field_set()


def test_explicit_field_filter_and_narrow_width(footer, monkeypatch):
    obj, _ = footer
    obj._status_bar_field_set_cache = frozenset({"model", "muse"})
    assert "| 96%" in obj._build_status_bar_text(200)
    assert "$9.259" not in obj._build_status_bar_text(200)
    for width in (40, 60, 100):
        monkeypatch.setattr(obj, "_get_tui_terminal_width", lambda: width)
        text = "".join(part for _, part in obj._get_status_bar_fragments())
        assert "96%" in text
        assert len(text) <= width


def test_ai_usage_status_shows_all_accounts_and_muse(footer, monkeypatch):
    obj, _ = footer
    output = []
    monkeypatch.setattr(obj, "_console_print", output.append)
    obj._handle_ai_usage_command("/ai-usage status")
    assert "Grok 72%, 0%, 0%" in output[0]
    assert "Meta/Muse 96%" in output[0]
    assert "OpenRouter $9.259" in output[0]
