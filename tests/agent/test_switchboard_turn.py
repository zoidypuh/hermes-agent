"""Native beta delivery owns one admitted Hermes turn and cannot replay its helper."""
import threading
import time
from types import SimpleNamespace

import pytest

from agent import switchboard_stream, switchboard_turn
from agent.interrupt_control import InterruptControlMixin
from agent.stream_delivery import StreamDeliveryMixin


HEADER = "[V channel=beta tts=xai agent=vera] Say hello."
MARKER = switchboard_turn.FINAL_MARKER


class RecordingTransport:
    def __init__(self, *, reject_begin=False, uncertain_begin=False):
        self.events = []
        self.one_shots = []
        self.reject_begin = reject_begin
        self.uncertain_begin = uncertain_begin

    def post(self, payload):
        self.events.append(dict(payload))
        if payload["action"] == "begin":
            if self.uncertain_begin:
                raise TimeoutError("unknown delivery")
            if self.reject_begin:
                return {"ok": False, "accepted": False, "error": "unsupported"}
        sequence = payload.get("seq", 0)
        return {"ok": True, "accepted": True, "next_seq": sequence + (payload["action"] == "chunk")}

    def capabilities(self):
        return {"ok": True, "ready": True, "channel": "beta", "tts": "xai", "protocol": 1}

    def one_shot(self, payload):
        self.one_shots.append(dict(payload))
        return {"ok": True, "status": "queued"}

    def close(self):
        pass


def _until(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline, "delivery worker did not reach expected state"
        time.sleep(0.005)


def _native(monkeypatch, *, transport=None, current="old-turn", clean=HEADER, agent_type=SimpleNamespace):
    monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", "1")
    transport = transport or RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: transport)
    agent = agent_type(
        platform="tui", _parent_session_id=None, _current_turn_id=current,
        _relay_pending_turn_id="this-turn", _interrupt_requested=False,
        _stream_writer_token=1, _api_call_count=1,
    )
    displayed = []
    controller, api_message, persisted, callback = switchboard_turn.prepare_switchboard_turn(
        agent, "[TUI HUD]\n" + clean, clean, displayed.append, "this-turn",
    )
    assert controller is not None
    agent._current_turn_id = "this-turn"
    agent._relay_pending_turn_id = None
    agent._stream_callback = callback
    return agent, controller, callback, transport, displayed, api_message, persisted


class RedirectAgent(InterruptControlMixin, StreamDeliveryMixin):
    def __init__(self, **attributes):
        self.__dict__.update(attributes)
        self._pending_redirect_lock = threading.Lock()
        self._pending_redirect = None
        self._pending_steer_lock = threading.Lock()
        self._pending_steer = None
        self._model_request_active = threading.Event()
        self._model_request_active.set()
        self._execution_thread_id = None
        self.api_mode = "chat_completions"
        self.stream_delta_callback = None
        self._strip_think_blocks = lambda text: text


def _apply_redirect(agent):
    from agent.conversation_loop import _apply_active_turn_redirect
    assert agent.clear_interrupt(preserve_redirect=True)
    raw = agent._drain_pending_redirect()
    history = [{"role": "user", "content": HEADER, "api_content": "immutable original API input"}]
    _apply_active_turn_redirect(agent, history, raw)
    return history


@pytest.mark.parametrize("message", [
    "[V] hello", "[V channel=stable tts=xai agent=vera] hello",
    "[V channel=beta tts=mac agent=vera] hello", "[V channel=beta tts=gpu agent=vera] hello",
    "[V channel=beta agent=vera] hello", "[V channel=beta tts=xai agent=other] hello",
    "prefix " + HEADER, " " + HEADER, "[V channel=beta tts=xai agent=vera]suffix",
    "[V channel=beta channel=stable tts=xai agent=vera] hello", None,
])
def test_header_requires_current_complete_explicit_beta_xai_identity(message):
    assert switchboard_turn.parse_beta_header(message) is None


def test_header_preserves_exact_agent_and_engine():
    assert switchboard_turn.parse_beta_header("[V agent=mara tts=xai channel=beta] hello") == {
        "agent": "mara", "tts": "xai", "channel": "beta",
    }


@pytest.mark.parametrize("enabled,message,platform,parent", [
    (None, HEADER, "tui", None), ("true", HEADER, "tui", None),
    ("1", HEADER.replace("beta", "stable"), "tui", None),
    ("1", HEADER, "subagent", None), ("1", HEADER, "cli", "parent"),
])
def test_disabled_stable_and_child_turns_keep_the_original_helper_path(monkeypatch, enabled, message, platform, parent):
    if enabled is None:
        monkeypatch.delenv("HERMES_SWITCHBOARD_STREAMING", raising=False)
    else:
        monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", enabled)
    callback = lambda _: None
    agent = SimpleNamespace(platform=platform, _parent_session_id=parent)
    actual = switchboard_turn.prepare_switchboard_turn(agent, message, None, callback, "turn")
    assert actual == (None, message, None, callback)
    assert not hasattr(agent, "_switchboard_turn")


def test_tui_clean_header_native_callback_final_flush_and_one_message(monkeypatch):
    agent, ctl, callback, transport, displayed, api_message, persisted = _native(monkeypatch)
    try:
        assert persisted == HEADER
        assert api_message.startswith("[TUI HUD]\n" + HEADER)
        assert "native beta delivery is active" in api_message
        first = "[chuckle] This is the first complete sentence. "
        tail = "<soft>Tag spelling stays literal.</soft>"
        callback(MARKER + first)
        _until(lambda: any(event["action"] == "chunk" for event in transport.events))
        assert not any(event["action"] == "end" for event in transport.events)
        callback(tail)
        result = {"completed": True, "final_response": first + tail}
        ctl.finish(result)
        assert result["switchboard_delivery"]["completed"] is True
        assert [event["action"] for event in transport.events] == ["begin", "chunk", "chunk", "end"]
        assert len({event["stream_id"] for event in transport.events}) == 1
        assert len({event["message_id"] for event in transport.events}) == 1
        assert all(event["agent_id"] == "vera" for event in transport.events)
        assert "".join(event["text"] for event in transport.events if event["action"] == "chunk") == first + tail
        assert displayed == [first, tail]
    finally:
        ctl.close()


def test_physical_retry_discards_only_unsubmitted_prefix(monkeypatch):
    agent, ctl, callback, transport, *_ = _native(monkeypatch)
    try:
        callback(MARKER + "Discard this fragment")
        agent._stream_writer_token += 1
        final = "This new response replaces the unsubmitted fragment. "
        callback(MARKER + final)
        ctl.finish({"completed": True, "final_response": final})
        assert ctl.delivery.completed
        assert "".join(event["text"] for event in transport.events if event["action"] == "chunk") == final
    finally:
        ctl.close()


def test_physical_retry_after_clause_never_replays(monkeypatch):
    agent, ctl, callback, transport, *_ = _native(monkeypatch)
    try:
        text = "This complete sentence may already be playing. "
        callback(MARKER + text)
        _until(lambda: any(event["action"] == "chunk" for event in transport.events))
        agent._stream_writer_token += 1
        callback(MARKER + text)
        ctl.finish({"completed": True, "final_response": text})
        assert ctl.delivery.cancelled
        assert [event["action"] for event in transport.events].count("chunk") == 1
        assert not transport.one_shots
    finally:
        ctl.close()


def test_tool_before_any_clause_discards_commentary(monkeypatch):
    _, ctl, callback, transport, *_ = _native(monkeypatch)
    try:
        callback("Let me check")
        assert ctl.before_tool("terminal", {"command": "pwd"}) is None
        final = "The task is now finished. "
        callback(MARKER + final)
        ctl.finish({"completed": True, "final_response": final})
        assert ctl.delivery.completed
        assert "".join(event["text"] for event in transport.events if event["action"] == "chunk") == final
    finally:
        ctl.close()


def test_long_terminal_status_then_tool_then_marked_final_streams_only_final(monkeypatch):
    agent, ctl, callback, transport, displayed, *_ = _native(monkeypatch)
    try:
        status = "I will inspect the current beta audio route and check its cancellation handling before changing the adapter. "
        callback(status)
        assert displayed == [status]
        assert transport.events == []
        assert ctl.before_tool("terminal", {"command": "pwd"}) is None
        agent._stream_writer_token += 1
        first = "[chuckle] The adapter now sends the finished clauses in order. "
        tail = "<soft>The status update stayed in your terminal.</soft>"
        callback(MARKER[:9])
        callback(MARKER[9:17])
        assert displayed == [status]
        callback(MARKER[17:] + first)
        _until(lambda: any(event["action"] == "chunk" for event in transport.events))
        assert not any(event["action"] == "end" for event in transport.events)
        assert [event["text"] for event in transport.events if event["action"] == "chunk"] == [first]
        callback(tail)
        result = {"completed": True, "final_response": _production_final(monkeypatch, agent, MARKER + first + tail)}
        ctl.finish(result)
        assert result["switchboard_delivery"]["completed"] is True
        assert result["switchboard_delivery"]["final_boundary"] == "marked"
        assert "".join(displayed) == status + first + tail
        assert "".join(event["text"] for event in transport.events if event["action"] == "chunk") == first + tail
        assert len({event["message_id"] for event in transport.events}) == 1
        assert not transport.one_shots
    finally:
        ctl.close()


def test_missing_marker_sends_only_authoritative_final_at_completion(monkeypatch):
    agent, ctl, callback, transport, displayed, *_ = _native(monkeypatch)
    try:
        status = "I will check the exact running beta route before confirming whether it streams. "
        final = "[sigh] The final answer lacks a marker, so this one waits until it is complete. "
        callback(status)
        ctl.before_tool("terminal", {"command": "pwd"})
        agent._stream_writer_token += 1
        callback(final)
        assert transport.events == []
        result = {"completed": True, "final_response": final}
        ctl.finish(result)
        assert result["switchboard_delivery"]["final_boundary"] == "completed_text"
        assert "".join(event["text"] for event in transport.events if event["action"] == "chunk") == final
        assert displayed == [status, final]
        assert not transport.one_shots
    finally:
        ctl.close()


def test_tools_after_a_marked_clause_cancel_without_replay(monkeypatch):
    _, ctl, callback, transport, *_ = _native(monkeypatch)
    try:
        final = "This final sentence has already started delivery. "
        callback(MARKER + final)
        _until(lambda: any(event["action"] == "chunk" for event in transport.events))
        ctl.before_tool("terminal", {"command": "pwd"})
        callback(MARKER + "This changed final must not replay anything. ")
        ctl.finish({"completed": True, "final_response": "This changed final must not replay anything. "})
        assert ctl.delivery.cancelled
        assert len([event for event in transport.events if event["action"] == "chunk"]) == 1
        assert not transport.one_shots
    finally:
        ctl.close()


@pytest.mark.parametrize("command", [
    "cat /home/gismar/.hermes/skills/switchboard-voice/scripts/send_reply.py",
    "rg send_reply.py /home/gismar/.hermes/skills/switchboard-voice",
    "head -60 /home/gismar/.hermes/skills/switchboard-voice/scripts/send_reply.py",
])
def test_read_only_helper_inspection_stays_available(monkeypatch, command):
    _, ctl, _, _, *_ = _native(monkeypatch)
    try:
        assert ctl.before_tool("terminal", {"command": command}) is None
    finally:
        ctl.abort()
        ctl.close()


@pytest.mark.parametrize("name,args", [
    ("terminal", {"command": "python3 /home/gismar/.hermes/skills/switchboard-voice/scripts/send_reply.py --channel beta --agent vera"}),
    ("execute_code", {"code": "terminal(command='curl http://host:8868/api/assistant-reply')"}),
    ("delegate_task", {"tasks": [{"prompt": "Send using send_reply.py"}]}),
])
def test_real_tool_guard_blocks_legacy_replay_during_native_ownership(monkeypatch, name, args):
    from agent.tool_executor import _pre_tool_block

    agent, ctl, _, transport, *_ = _native(monkeypatch)
    try:
        message, actual_args = _pre_tool_block(agent, SimpleNamespace(name=name, args=args))
        assert "already owns this beta turn" in message
        assert actual_args is args
        assert transport.events == []
    finally:
        ctl.abort()
        ctl.close()


def test_tool_guard_preserves_unowned_turn_behavior(monkeypatch):
    from agent.tool_executor import _pre_tool_block
    from hermes_cli import plugins

    calls = []
    monkeypatch.setattr(plugins, "_dispatch_pre_tool_call_hooks", lambda *args, **kwargs: (calls.append(args) or (None, None)))
    monkeypatch.setattr("agent.tool_executor.tool_hook_ids", lambda *args: {})
    args = {"command": "python send_reply.py --channel stable --agent mara"}
    ref = SimpleNamespace(name="terminal", args=args, task_id="t", call_id="c", trace=[])
    assert _pre_tool_block(SimpleNamespace(), ref) == (None, args)
    assert calls[0] == ("terminal", args)


def test_interrupt_latch_survives_finalizer_clear_and_rejects_late_delta(monkeypatch):
    from agent.interrupt_control import InterruptControlMixin

    agent, ctl, callback, transport, *_ = _native(monkeypatch)
    agent._execution_thread_id = None
    agent._active_children_lock = threading.Lock()
    agent._active_children = set()
    agent.quiet_mode = True
    try:
        callback("Unsubmitted partial")
        assert InterruptControlMixin.interrupt(agent) is True
        assert ctl.is_cancelled()
        agent._interrupt_requested = False  # finalize_turn clears this before facade returns
        callback(" stale continuation")
        ctl.finish({"completed": True, "final_response": "Unsubmitted partial stale continuation"})
        assert ctl.delivery.cancelled
        assert transport.events == []
    finally:
        ctl.close()


def test_old_controller_cannot_feed_a_new_turn(monkeypatch):
    agent, ctl, callback, transport, *_ = _native(monkeypatch)
    try:
        agent._current_turn_id = "next-turn"
        callback("A late response from the old turn must be silent. ")
        ctl.delivery.wait(1)
        assert ctl.delivery.cancelled
        assert transport.events == []
    finally:
        ctl.close()


@pytest.mark.parametrize("uncertain,expect_fallback", [(False, True), (True, False)])
def test_only_definitive_pre_begin_failure_allows_one_shot(monkeypatch, uncertain, expect_fallback):
    transport = RecordingTransport(reject_begin=not uncertain, uncertain_begin=uncertain)
    _, ctl, callback, _, *_ = _native(monkeypatch, transport=transport)
    try:
        final = "[chuckle] This text must be sent exactly once. "
        callback(MARKER + final)
        result = {"completed": True, "final_response": final}
        ctl.finish(result)
        assert len(transport.one_shots) == int(expect_fallback)
        if expect_fallback:
            assert transport.one_shots[0]["text"] == final
        else:
            assert ctl.delivery.uncertain
        ctl.finish(result)
        assert len(transport.one_shots) == int(expect_fallback)
    finally:
        ctl.close()


def _facade_agent(monkeypatch):
    from run_agent import AIAgent
    from agent import relay_runtime, turn_facade_lease
    from hermes_cli.observability import relay_shared_metrics  # register with the real coordinator first

    agent = AIAgent.__new__(AIAgent)
    agent.session_id, agent.platform, agent.model = "session", "tui", "test-model"
    agent._parent_session_id = None
    agent._session_db = None
    agent._interrupt_requested = False
    agent._conversation_root_id = lambda: "session"
    agent._reset_activity_labels_after_turn = lambda: None
    agent._stream_writer_token, agent._api_call_count = 1, 1
    monkeypatch.setattr("agent.background_review.cancel_background_review_for_live_turn", lambda _: None)
    monkeypatch.setattr(turn_facade_lease, "admit_durable_turn_lease", lambda _, **kw: SimpleNamespace(
        early_result=None, lease=None, conversation_history=kw["conversation_history"],
    ))
    coordinator = SimpleNamespace(
        acquire_conversation=lambda **kw: object(),
        begin_turn=lambda *args, **kw: SimpleNamespace(relay_enabled=False),
        finish_logical_calls=lambda *args, **kw: None,
        end_turn=lambda *args, **kw: None,
        release_conversation=lambda *args, **kw: None,
    )
    monkeypatch.setattr(relay_runtime, "SESSION_COORDINATOR", coordinator)
    return agent


def _production_final(monkeypatch, agent, text):
    """Exercise the real final-text cleanup; do not duplicate its strip in the test."""
    from agent import turn_final_response

    monkeypatch.setattr("agent.agent_runtime_helpers.intent_ack_continuation_mode", lambda _: "off")
    monkeypatch.setattr(turn_final_response, "apply_stop_gates", lambda *args, **kw: SimpleNamespace(
        pending_verification_response=None, pending_verification_response_previewed=False, continue_turn=False,
    ))
    agent.valid_tool_names = []
    agent._has_content_after_think_block = lambda value: bool(value.strip())
    agent._emit_pending_fallback_notice = lambda: None
    agent._clear_status_buffer = lambda: None
    agent._strip_think_blocks = lambda value: value
    agent._build_assistant_message = lambda response, reason: {"role": "assistant", "content": response.content}
    agent._flush_messages_to_session_db = lambda *args: True
    agent.quiet_mode = True
    verdict = turn_final_response.finish_text_response(
        agent, assistant_message=SimpleNamespace(content=text, tool_calls=[]), response=None,
        finish_reason="stop", messages=[{"role": "user", "content": HEADER}], api_messages=[],
        conversation_history=[], api_call_count=1, user_message=HEADER, active_system_prompt="stable",
        final_response=None, _turn_exit_reason=None, _preflight_compression_blocked=False,
        codex_ack_continuations=0, truncated_response_parts=[], length_continue_retries=0,
        _pending_verification_response=None, _pending_verification_response_previewed=False,
    )
    assert verdict.action == "break"
    return verdict.final_response


def test_shared_facade_preserves_system_and_original_message_and_closes_turn(monkeypatch):
    from agent import conversation_loop

    agent = _facade_agent(monkeypatch)
    monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", "1")
    transport = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: transport)
    seen = {}
    final = "[sigh] The final reply is sent through native deltas. "

    def run_loop(bound_agent, message, system, history, task, callback, persist, **kwargs):
        seen.update(message=message, system=system, history=history, persist=persist, callback=callback)
        bound_agent._current_turn_id = bound_agent._relay_pending_turn_id
        bound_agent._relay_pending_turn_id = None
        callback(MARKER + final)
        return {"completed": True, "final_response": _production_final(monkeypatch, bound_agent, MARKER + final), "messages": []}

    monkeypatch.setattr(conversation_loop, "run_conversation", run_loop)
    displayed, history = [], [{"role": "user", "content": "old prompt"}]
    result = agent.run_conversation("[HUD]\n" + HEADER, system_message="immutable system",
                                    conversation_history=history, persist_user_message=HEADER,
                                    stream_callback=displayed.append)
    assert seen["persist"] == HEADER
    assert seen["system"] == "immutable system"
    assert seen["history"] is history
    assert displayed == [final]
    assert result["switchboard_delivery"]["completed"] is True
    assert result["final_response"] == final.strip()
    assert agent._switchboard_turn is None
    seen["callback"]("This late text must never go to Relay. ")
    assert [event["action"] for event in transport.events] == ["begin", "chunk", "end"]


def test_shared_facade_exception_aborts_and_cleans_ownership(monkeypatch):
    from agent import conversation_loop

    agent = _facade_agent(monkeypatch)
    monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", "1")
    transport = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: transport)
    seen = {}

    def run_loop(bound_agent, message, system, history, task, callback, persist, **kwargs):
        seen["controller"] = bound_agent._switchboard_turn
        raise RuntimeError("provider failed")

    monkeypatch.setattr(conversation_loop, "run_conversation", run_loop)
    with pytest.raises(RuntimeError, match="provider failed"):
        agent.run_conversation(HEADER)
    assert agent._switchboard_turn is None
    assert seen["controller"].delivery.wait(1)["cancelled"] is True
    assert transport.events == []


@pytest.mark.parametrize("marked", [False, True])
def test_real_redirect_replaces_cancelled_adapter_and_delivers_final(monkeypatch, marked):
    agent, old, old_callback, old_transport, displayed, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    fresh = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: fresh)
    try:
        old_callback("I will inspect the current route before answering. ")
        assert agent.redirect(HEADER)
        assert old.delivery.wait(1)["cancelled"]
        history = _apply_redirect(agent)
        assert history[0] == {"role": "user", "content": HEADER, "api_content": "immutable original API input"}
        assert history[-1]["content"] == HEADER
        assert switchboard_turn._RUNTIME_NOTE in history[-1]["api_content"]
        current = agent._switchboard_turn
        assert current is not old and old.closed
        old_callback(MARKER + "Late old text must never reach the replacement stream. ")
        final = "[chuckle] The redirected final now has a fresh delivery. "
        agent._stream_callback((MARKER if marked else "") + final)
        if marked:
            _until(lambda: any(event["action"] == "chunk" for event in fresh.events))
            assert not any(event["action"] == "end" for event in fresh.events)
        else:
            assert fresh.events == []
        current.finish({"completed": True, "final_response": _production_final(monkeypatch, agent, final)})
        assert current.delivery.completed
        assert old_transport.events == []
        expected = final if marked else final.strip()
        assert "".join(event["text"] for event in fresh.events if event["action"] == "chunk") == expected
        assert not old_transport.one_shots and not fresh.one_shots
    finally:
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


@pytest.mark.parametrize("uncertain", [False, True])
def test_redirect_after_delivery_attempt_cancels_old_without_replaying(monkeypatch, uncertain):
    transport = RecordingTransport(uncertain_begin=uncertain)
    agent, old, callback, _, *_ = _native(monkeypatch, transport=transport, agent_type=RedirectAgent)
    fresh = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: fresh)
    try:
        callback(MARKER + "This is the old reply that the correction replaces. ")
        _until(lambda: old.delivery.uncertain if uncertain else any(e["action"] == "chunk" for e in transport.events))
        assert agent.redirect(HEADER.replace("agent=vera", "agent=mara"))
        _apply_redirect(agent)
        current = agent._switchboard_turn
        final = "<soft>This corrected answer is for the current Mara header.</soft>"
        agent._stream_callback(MARKER + final)
        current.finish({"completed": True, "final_response": final})
        assert current.delivery.completed
        assert any(e["action"] == "cancel" for e in transport.events)
        assert not any(e["action"] == "end" for e in transport.events)
        assert all(e["agent_id"] == "mara" for e in fresh.events)
        assert {e["stream_id"] for e in transport.events}.isdisjoint(e["stream_id"] for e in fresh.events)
        assert not transport.one_shots and not fresh.one_shots
    finally:
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


@pytest.mark.parametrize("latest,expected_agent", [
    (HEADER.replace("agent=vera", "agent=mara"), "mara"),
    (HEADER.replace("beta", "stable"), None),
    (HEADER.replace("tts=xai", "tts=mac"), None),
    ("Please answer this correction normally.\nQuoted example: " + HEADER, None),
])
def test_multiple_pending_redirects_route_only_latest_raw_input(monkeypatch, latest, expected_agent):
    agent, old, _, transport, displayed, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    fresh = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: fresh)
    try:
        assert agent.redirect(HEADER)
        assert agent.redirect(latest)
        history = _apply_redirect(agent)
        assert history[-1]["content"] == HEADER + "\n\n[Additional user correction]\n" + latest
        assert switchboard_turn._latest_route_note(latest) in history[-1]["api_content"]
        if expected_agent:
            current = agent._switchboard_turn
            final = "The newest correction selects the speech destination. "
            agent._stream_callback(MARKER + final)
            current.finish({"completed": True, "final_response": final})
            assert all(e["agent_id"] == expected_agent for e in fresh.events)
        else:
            assert agent._switchboard_turn is None
            assert agent._stream_callback == displayed.append
            assert switchboard_turn._RELEASE_NOTE in history[-1]["api_content"]
            assert fresh.events == []
        assert old.closed and transport.events == []
    finally:
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


def test_redirect_routing_snapshot_does_not_consume_later_pending_input(monkeypatch):
    from agent.conversation_loop import _apply_active_turn_redirect
    agent, old, _, _, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    fresh = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: fresh)
    try:
        first = HEADER.replace("agent=vera", "agent=mara")
        second = HEADER.replace("beta", "stable")
        assert agent.redirect(first)
        assert agent.clear_interrupt(preserve_redirect=True)
        drained = agent._drain_pending_redirect()
        assert agent.redirect(second)
        history = [{"role": "user", "content": HEADER}]
        _apply_active_turn_redirect(agent, history, drained)
        assert agent._switchboard_turn.delivery._identity["agent_id"] == "mara"
        assert agent._switchboard_pending_inputs["redirect"][0] == second
        assert agent.clear_interrupt(preserve_redirect=True)
        _apply_active_turn_redirect(agent, history, agent._drain_pending_redirect())
        assert agent._switchboard_turn is None
        assert switchboard_turn._RELEASE_NOTE in history[-1]["api_content"]
    finally:
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


@pytest.mark.parametrize("consumer", ["batch", "pre_api"])
def test_tool_phase_redirect_rebinds_when_steer_is_consumed(monkeypatch, consumer):
    from agent.agent_runtime_helpers import apply_pending_steer_to_tool_results
    from agent.turn_iteration_prep import _inject_steer_after_newest_tool_result
    agent, old, callback, transport, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    fresh = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: fresh)
    try:
        callback("I will inspect the tool result before answering. ")
        agent._executing_tools = True
        latest = HEADER.replace("agent=vera", "agent=mara")
        assert agent.redirect(latest)
        assert agent._interrupt_requested is False
        messages = [{"role": "tool", "content": "Tool finished."}]
        if consumer == "batch":
            apply_pending_steer_to_tool_results(agent, messages, 1)
        else:
            _inject_steer_after_newest_tool_result(agent, messages, agent._drain_pending_steer())
        current = agent._switchboard_turn
        assert current is not old
        assert messages[0]["content"] == "Tool finished."
        assert messages[-1]["role"] == "user"
        assert latest in messages[-1]["content"]
        assert switchboard_turn._RUNTIME_NOTE in messages[-1]["content"]
        agent._executing_tools = False
        final = "The finished tool result answers your newest correction. "
        agent._stream_callback(MARKER + final)
        current.finish({"completed": True, "final_response": final})
        assert current.delivery.completed
        assert all(e["agent_id"] == "mara" for e in fresh.events)
        assert transport.events == []
    finally:
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


@pytest.mark.parametrize("enabled,text,kind", [
    (None, HEADER, "redirect"), (None, "A normal correction.", "steer"),
    ("1", "A normal correction.", "steer"), ("1", "A normal correction.", "redirect"),
])
def test_unowned_ordinary_steer_does_not_touch_callbacks_or_writer(monkeypatch, enabled, text, kind):
    if enabled:
        monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", enabled)
    else:
        monkeypatch.delenv("HERMES_SWITCHBOARD_STREAMING", raising=False)
    callback = lambda text: None
    flushes = []
    agent = RedirectAgent(platform="cli", _stream_writer_token=17, _stream_callback=callback,
                          _stream_think_scrubber=SimpleNamespace(flush=lambda: flushes.append(True)))
    before = dict(vars(agent))
    switchboard_turn.queue_switchboard_redirect(agent, text, kind=kind)
    result = switchboard_turn.apply_switchboard_redirect(agent, text, "api body", kind=kind)
    assert result == "api body" and vars(agent) == before and flushes == []


def test_redirect_fences_old_provider_tls_and_discards_scrubber_tail(monkeypatch):
    agent, old, _, old_transport, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    fresh = RecordingTransport()
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: fresh)
    worker_ready, fire_stale, worker_done = threading.Event(), threading.Event(), threading.Event()

    def old_provider():
        agent._claim_stream_writer()
        worker_ready.set()
        assert fire_stale.wait(2)
        agent._fire_stream_delta(MARKER + "Stale provider text must never enter the replacement. ")
        worker_done.set()

    thread = threading.Thread(target=old_provider)
    thread.start()
    try:
        assert worker_ready.wait(2)
        tails = ["<"]
        agent._stream_think_scrubber = SimpleNamespace(flush=lambda: tails.pop() if tails else "")
        assert agent.redirect(HEADER)
        _apply_redirect(agent)
        assert tails == []
        fire_stale.set()
        assert worker_done.wait(2)
        assert agent._switchboard_turn.boundary == "pending"
        assert fresh.events == []
        agent._claim_stream_writer()
        final = "The fresh writer streams after the old one was fenced. "
        agent._stream_callback(MARKER + final)
        agent._switchboard_turn.finish({"completed": True, "final_response": final})
        assert "".join(e["text"] for e in fresh.events if e["action"] == "chunk") == final
        assert old_transport.events == []
    finally:
        fire_stale.set()
        thread.join(2)
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


@pytest.mark.parametrize("initial", [HEADER, "Ordinary typed terminal question."])
def test_facade_finishes_replacement_controller_after_redirect(monkeypatch, initial):
    from agent import conversation_loop
    agent = _facade_agent(monkeypatch)
    monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", "1")
    transports = []
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: (transports.append(RecordingTransport()) or transports[-1]))
    seen = {}
    final = "The facade finishes the replacement controller, including a newly voiced correction. "

    def run_loop(bound_agent, message, system, history, task, callback, persist, **kwargs):
        bound_agent._current_turn_id = bound_agent._relay_pending_turn_id
        bound_agent._stream_callback = callback
        bound_agent._strip_think_blocks = lambda text: text
        switchboard_turn.queue_switchboard_redirect(bound_agent, HEADER)
        switchboard_turn.take_switchboard_input(bound_agent, HEADER, kind="redirect")
        conversation_loop._apply_active_turn_redirect(bound_agent, [], HEADER)
        seen["current"] = bound_agent._switchboard_turn
        bound_agent._stream_callback(MARKER + final)
        bound_agent._stream_callback = None  # production finalizer clears this before facade.finish
        return {"completed": True, "final_response": _production_final(monkeypatch, bound_agent, MARKER + final)}

    monkeypatch.setattr(conversation_loop, "run_conversation", run_loop)
    result = agent.run_conversation(initial)
    assert result["switchboard_delivery"]["completed"]
    assert seen["current"].closed and agent._switchboard_turn is None
    actions = [e["action"] for e in transports[-1].events]
    assert actions.count("begin") == actions.count("end") == 1
    assert "".join(e["text"] for e in transports[-1].events if e["action"] == "chunk") == final
    assert all(not transport.events for transport in transports[:-1])


def test_queued_native_then_headerless_correction_does_not_reuse_first_header(monkeypatch):
    monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", "1")
    agent = RedirectAgent(platform="cli", _current_turn_id="turn", _interrupt_requested=False)
    latest = "This latest correction needs a normal terminal answer."
    assert agent.redirect(HEADER)
    assert agent.redirect(latest)
    history = _apply_redirect(agent)
    assert getattr(agent, "_switchboard_turn", None) is None
    assert switchboard_turn._latest_route_note(latest) in history[-1]["api_content"]


def test_facade_exception_aborts_replacement_controller(monkeypatch):
    from agent import conversation_loop
    agent = _facade_agent(monkeypatch)
    monkeypatch.setenv("HERMES_SWITCHBOARD_STREAMING", "1")
    transports = []
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: (transports.append(RecordingTransport()) or transports[-1]))
    seen = {}

    def run_loop(bound_agent, message, system, history, task, callback, persist, **kwargs):
        bound_agent._current_turn_id = bound_agent._relay_pending_turn_id
        bound_agent._stream_callback = callback
        switchboard_turn.queue_switchboard_redirect(bound_agent, HEADER)
        switchboard_turn.take_switchboard_input(bound_agent, HEADER, kind="redirect")
        switchboard_turn.apply_switchboard_redirect(bound_agent, HEADER, HEADER)
        seen["replacement"] = bound_agent._switchboard_turn
        raise RuntimeError("replacement provider failed")

    monkeypatch.setattr(conversation_loop, "run_conversation", run_loop)
    with pytest.raises(RuntimeError, match="replacement provider failed"):
        agent.run_conversation(HEADER)
    assert seen["replacement"].closed
    assert seen["replacement"].delivery.wait(1)["cancelled"]
    assert agent._switchboard_turn is None
    assert all(not transport.events for transport in transports)


def test_newer_steer_during_cancel_drain_fences_older_replacement(monkeypatch):
    from agent.conversation_loop import _apply_active_turn_redirect
    from agent.agent_runtime_helpers import apply_pending_steer_to_tool_results
    agent, old, _, old_transport, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    transports = []
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: (transports.append(RecordingTransport()) or transports[-1]))
    first = HEADER.replace("agent=vera", "agent=mara")
    second = HEADER
    try:
        assert agent.redirect(first)
        assert agent.clear_interrupt(preserve_redirect=True)
        first_drained = agent._drain_pending_redirect()
        original_wait = old.delivery.wait

        def queue_newer_during_wait(timeout):
            assert agent.steer(second)
            return original_wait(timeout)

        monkeypatch.setattr(old.delivery, "wait", queue_newer_during_wait)
        _apply_active_turn_redirect(agent, [], first_drained)
        stale = agent._switchboard_turn
        assert stale.is_cancelled() and not stale.is_current()
        assert agent._interrupt_requested is False
        stale.on_delta(MARKER + "The old correction must not begin a reply after the newer steer. ")
        stale.finish({"completed": True, "final_response": "The old correction must stay silent."})
        assert transports[0].events == [] and transports[0].one_shots == []
        assert agent._switchboard_pending_inputs["steer"][0] == second
        apply_pending_steer_to_tool_results(agent, [{"role": "tool", "content": "Done."}], 1)
        current = agent._switchboard_turn
        final = "The latest accepted correction owns this final reply. "
        current.on_delta(MARKER + final)
        current.finish({"completed": True, "final_response": final})
        assert current.delivery.completed
        assert all(e["agent_id"] == "vera" for e in transports[-1].events)
        assert old_transport.events == []
    finally:
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()


def test_soft_redirect_keeps_later_native_steer_and_its_generation(monkeypatch):
    from agent.conversation_loop import _apply_active_turn_redirect
    from agent.agent_runtime_helpers import apply_pending_steer_to_tool_results
    agent, old, _, _, *_ = _native(monkeypatch, agent_type=RedirectAgent)
    transports = []
    monkeypatch.setattr(switchboard_stream, "RelayHttpTransport", lambda: (transports.append(RecordingTransport()) or transports[-1]))
    try:
        first = HEADER.replace("agent=vera", "agent=mara")
        assert agent.redirect(first)
        assert agent.steer(HEADER)
        assert agent.clear_interrupt(preserve_redirect=True)
        assert agent._pending_steer == HEADER
        _apply_active_turn_redirect(agent, [], agent._drain_pending_redirect())
        assert agent._switchboard_turn.is_cancelled()
        apply_pending_steer_to_tool_results(agent, [{"role": "tool", "content": "Done."}], 1)
        current = agent._switchboard_turn
        final = "The later accepted steer remains available and owns this reply. "
        current.on_delta(MARKER + final)
        current.finish({"completed": True, "final_response": final})
        assert current.delivery.completed
        assert transports[0].events == []
        assert all(e["agent_id"] == "vera" for e in transports[-1].events)
    finally:
        old.close()
        if agent._switchboard_turn is not None:
            agent._switchboard_turn.close()
