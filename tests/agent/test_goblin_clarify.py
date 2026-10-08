"""Goblin questions must reach their originating chat, never the terminal picker."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

from agent import goblin_clarify as goblin
from agent.inline_tool_executors import INLINE_TOOL_EXECUTORS, InlineToolContext
from agent.switchboard_turn import prepare_switchboard_turn, apply_switchboard_redirect

UID = "807c6dde-253f-4754-92f5-419b89319a6c"
HEADER = f"[Goblin request agent=mara uid={UID} reply=text clarify=http://127.0.0.1:8869] Task: test"


def test_real_inline_dispatch_uses_current_request_and_restores_cli(monkeypatch):
    agent = SimpleNamespace(platform="cli", clarify_callback=Mock(return_value={"answers":{"q0":"CLI"},"outcome":"submitted"}))
    requests = []

    def post(url, payload):
        requests.append((url, payload))
        return {"ok":True,"answers":{"q0":"Correct model"},"outcome":"submitted"}

    monkeypatch.setattr(goblin, "_post", post)
    prepare_switchboard_turn(agent, HEADER, None, None, "turn")
    result = json.loads(INLINE_TOOL_EXECUTORS["clarify"](agent, {"questions":[{"question":"Which model?","choices":["Correct model","Other"]}]}, InlineToolContext("task")))
    assert result["responses"][0]["user_response"] == "Correct model"
    agent.clarify_callback.assert_not_called()
    assert requests[0][1]["uid"] == UID
    assert requests[0][1]["questions"][0]["choices"][0].endswith("(Recommended)")
    apply_switchboard_redirect(agent, "ordinary terminal message", "ordinary terminal message")
    assert goblin.callback_for(agent) is agent.clarify_callback
    prepare_switchboard_turn(agent, HEADER, None, None, "second-turn")
    goblin.clear_request(agent, "rejected-overlapping-turn")
    assert agent._goblin_request["uid"] == UID
    goblin.clear_request(agent, "second-turn")
    assert goblin.callback_for(agent) is agent.clarify_callback
    prepare_switchboard_turn(agent, "normal", None, None, "third-turn")
    assert goblin.callback_for(agent) is agent.clarify_callback


def test_no_answer_never_selects_recommended_or_opens_cli(monkeypatch):
    from tools import clarify_gateway
    monkeypatch.setattr(clarify_gateway, "get_clarify_timeout", lambda:1)
    agent = SimpleNamespace(platform="cli", clarify_callback=Mock())
    goblin.bind_request(agent, HEADER)
    posts = []

    def post(url, payload):
        posts.append(payload)
        return {"ok":True,"answers":{},"outcome":payload.get("outcome", "pending")}

    monkeypatch.setattr(goblin, "_post", post)
    ticks = iter([0, 2])
    monkeypatch.setattr(goblin.time, "monotonic", lambda:next(ticks))
    result = goblin.callback_for(agent)([{"qid":"q0","question":"Which?","choices":["Recommended"]}])
    assert result["outcome"] == "timed_out"
    assert result["answers"] == {}
    assert posts[-1]["action"] == "close"
    agent.clarify_callback.assert_not_called()
    goblin.bind_request(agent, f"[Goblin request agent=mara uid={UID} reply=text] Task: old request")
    assert goblin.callback_for(agent)([])['outcome'] == 'undelivered'


def test_interrupt_failure_and_subagent_isolation(monkeypatch):
    agent = SimpleNamespace(platform="cli", clarify_callback=Mock(), _interrupt_requested=True)
    goblin.bind_request(agent, HEADER)
    monkeypatch.setattr(goblin, "_post", lambda url, payload: {"ok":True,"answers":{},"outcome":payload.get("outcome","pending")})
    assert goblin.callback_for(agent)([])['outcome'] == 'cancelled'
    monkeypatch.setattr(goblin, "_post", Mock(side_effect=OSError('offline')))
    assert goblin.callback_for(agent)([])['outcome'] == 'undelivered'
    agent.platform = 'subagent'
    goblin.bind_request(agent, HEADER)
    assert goblin.callback_for(agent) is agent.clarify_callback
