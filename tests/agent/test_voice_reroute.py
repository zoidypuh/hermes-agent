from agent.voice_reroute import (
    ASSISTANT_REPLY_URL,
    prepend_voice_reroute,
    resolve_agent_id,
    voice_reroute_prefix,
)


def test_prefix_uses_agent_name_and_placeholder():
    text = voice_reroute_prefix("vera")
    assert ASSISTANT_REPLY_URL in text
    assert '\\"agent_id\\": \\"vera\\"' in text
    assert "<YOUR_ANSWER_TEXT>" in text
    assert "remember to send your answer as text over" in text


def test_prepend_string_and_multimodal(monkeypatch):
    monkeypatch.setenv("HERMES_PROFILE", "vera")
    assert resolve_agent_id() == "vera"
    out = prepend_voice_reroute("hello")
    assert out.endswith("hello")
    assert ASSISTANT_REPLY_URL in out
    parts = prepend_voice_reroute([{"type": "text", "text": "hi"}])
    assert parts[0]["text"].endswith("hi")
    assert "vera" in parts[0]["text"]
