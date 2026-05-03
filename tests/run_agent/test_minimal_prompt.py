from __future__ import annotations


def _agent(monkeypatch, tmp_path, **kwargs):
    home = tmp_path / ".hermes"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    from pathlib import Path as _Path
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)

    from run_agent import AIAgent

    defaults = dict(
        api_key="test",
        base_url="https://openrouter.ai/api/v1",
        model="test/model",
        provider="test-provider",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        enabled_toolsets=[],
    )
    defaults.update(kwargs)
    return AIAgent(**defaults)


def test_minimal_prompt_omits_generic_runtime_bloat(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path, minimal_prompt=True)

    prompt = agent._build_system_prompt()

    assert "You are Hermes Agent" in prompt
    assert "If the user asks about configuring" not in prompt
    assert "Conversation started:" not in prompt
    assert "Model: test/model" not in prompt
    assert "Provider: test-provider" not in prompt


def test_normal_prompt_keeps_runtime_guidance(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path, minimal_prompt=False)

    prompt = agent._build_system_prompt()

    assert "If the user asks about configuring" in prompt
    assert "Conversation started:" in prompt
    assert "Model: test/model" in prompt
    assert "Provider: test-provider" in prompt
