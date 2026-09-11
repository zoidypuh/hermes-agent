"""Unit tests for agent.ai_usage (status-bar quota / credits read-outs)."""

from unittest.mock import patch

from agent.ai_usage import (
    CreditsStatus,
    UsageStatus,
    clear_cache,
    credits_category,
    format_chatgpt,
    format_grok,
    format_openrouter,
    read_chatgpt_usage,
    read_grok_usage,
    read_openrouter_credits,
)


def setup_function(_fn):
    clear_cache()


def test_format_openrouter_comma_dollar():
    assert format_openrouter(CreditsStatus(available=True, remaining=8.49)) == "8,49$"
    assert format_openrouter(CreditsStatus(available=True, remaining=8.4)) == "8,40$"
    assert format_openrouter(CreditsStatus(available=True, remaining=0)) == "0,00$"
    assert format_openrouter(CreditsStatus(available=False)) == ""
    assert format_openrouter(CreditsStatus(available=True, remaining=None)) == ""


def test_credits_category_low_first():
    assert credits_category(CreditsStatus(available=True, remaining=0.5)) == "critical"
    assert credits_category(CreditsStatus(available=True, remaining=3.0)) == "bad"
    assert credits_category(CreditsStatus(available=True, remaining=10.0)) == "warn"
    assert credits_category(CreditsStatus(available=True, remaining=50.0)) == "good"
    assert credits_category(CreditsStatus(available=False)) == "dim"


def test_read_openrouter_credits_from_usage_api():
    payload = {"openrouter": {"online": True, "remainingCredits": 8.49}}
    with patch("agent.usage_api.fetch_usage", return_value=payload):
        status = read_openrouter_credits(use_cache=False)
    assert status.available is True
    assert abs(status.remaining - 8.49) < 1e-9
    assert format_openrouter(status) == "8,49$"


def test_read_openrouter_hides_when_api_offline():
    with patch("agent.usage_api.fetch_usage", return_value=None):
        status = read_openrouter_credits(use_cache=False)
    assert status.available is False
    assert format_openrouter(status) == ""


def test_read_chatgpt_and_grok_from_usage_api():
    payload = {
        "chatgpt": {"online": True, "remaining": 75},
        "grok": {"online": True, "remaining": 51},
        "openrouter": {"online": False},
    }
    with patch("agent.usage_api.fetch_usage", return_value=payload):
        assert format_chatgpt(read_chatgpt_usage(use_cache=False)) == "75%"
        assert format_grok(read_grok_usage(use_cache=False)) == "51%"
        assert format_openrouter(read_openrouter_credits(use_cache=False)) == ""


def test_usage_status_unchanged():
    assert UsageStatus(available=True, remaining=76).remaining == 76
