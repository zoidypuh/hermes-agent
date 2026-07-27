from hermes_cli.oneshot import _request_options_for_oneshot


def test_oneshot_request_options_preserve_runtime_overrides():
    cfg = {"agent": {"reasoning_effort": "high"}}
    runtime = {"request_overrides": {"extra_body": {"custom": True}}}

    reasoning, service_tier, overrides = _request_options_for_oneshot(
        cfg,
        "gpt-5.4",
        runtime,
    )

    assert reasoning == {"enabled": True, "effort": "high"}
    assert service_tier is None
    assert overrides == {"extra_body": {"custom": True}}


def test_oneshot_request_options_apply_fast_mode(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.models.resolve_fast_mode_overrides",
        lambda model: {"speed": "fast"} if model == "claude-opus-4-6" else None,
    )
    cfg = {
        "agent": {
            "reasoning_effort": False,
            "service_tier": "fast",
        }
    }

    reasoning, service_tier, overrides = _request_options_for_oneshot(
        cfg,
        "claude-opus-4-6",
        {},
    )

    assert reasoning == {"enabled": False}
    assert service_tier == "priority"
    assert overrides == {"speed": "fast"}


def test_oneshot_request_options_ignore_unknown_service_tier():
    reasoning, service_tier, overrides = _request_options_for_oneshot(
        {"agent": {"service_tier": "warp"}},
        "gpt-5.4",
        {},
    )

    assert reasoning is None
    assert service_tier is None
    assert overrides is None
