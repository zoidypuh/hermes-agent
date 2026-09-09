"""Opt-in Switchboard beta delivery at Hermes' existing per-turn TTS callback.

The transport owns delivery state. This module owns the clean inbound header,
native callback lifetime, and the legacy-helper exclusion for that same turn.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
import uuid
from contextlib import nullcontext
from typing import Any

logger = logging.getLogger(__name__)
_HEADER = re.compile(r"\A\[V\s+([^\]\r\n]+)\](?:\s|$)")
_LEGACY_SEND = re.compile(r"(?:send_reply\.py|/api/assistant-reply(?:/|\b)|\bmara-say\b)")
_EXECUTION_TOOLS = frozenset({"terminal", "execute_code", "delegate_task"})
FINAL_MARKER = "[[SWITCHBOARD_FINAL]]"
_RUNTIME_NOTE = (
    "[Switchboard native beta delivery is active for this turn. "
    "For a complex, research, or tool task, give exactly one short concrete terminal-only work "
    "update before the first tool call. Leave that update unmarked; it is never spoken. "
    "Complete all requested work and tool calls first. Then start the final spoken answer with "
    "the exact prefix [[SWITCHBOARD_FINAL]] followed by your spoken text. For simple conversation, "
    "start directly with that prefix and the answer. The runtime removes the prefix. "
    "The runtime sends the final answer's text deltas to the current header's exact agent and "
    "channel; preserve every speech tag literally and do not speak the header. "
    "Do not call send_reply.py, /api/assistant-reply, mara-say, or delegate reply delivery: "
    "the runtime owns sending and any safe one-shot fallback. "
    "Do not repeat or summarize the reply after sending. Do not use tools after the final prefix.]"
)
_RELEASE_NOTE = (
    "[Switchboard native delivery is inactive for this current correction. "
    "Earlier native-delivery instructions apply only to the interrupted input. "
    "Use only this correction's own voice header, if any, and its normal reply helper; "
    "without a voice header respond normally. Do not emit [[SWITCHBOARD_FINAL]].]"
)


def _header_fields(message: Any) -> dict[str, str] | None:
    if not isinstance(message, str):
        return None
    match = _HEADER.match(message)
    if match is None:
        return None
    values: dict[str, str] = {}
    for field in match.group(1).split():
        key, sep, value = field.partition("=")
        if not sep or key in values:
            return None
        values[key] = value
    return values


def parse_beta_header(message: Any) -> dict[str, str] | None:
    """Only the current clean, complete beta/xAI header can authorize delivery."""
    values = _header_fields(message)
    if values is None:
        return None
    if (
        values.get("channel") != "beta"
        or values.get("tts") != "xai"
        or values.get("agent") not in {"mara", "vera"}
    ):
        return None
    return values


def _latest_route_note(message: str) -> str:
    fields = _header_fields(message) or {}
    if (fields.get("channel") in {"beta", "stable"} and fields.get("tts") in {"xai", "mac", "gpu"}
            and fields.get("agent") in {"mara", "vera"}):
        route = f"[V channel={fields['channel']} tts={fields['tts']} agent={fields['agent']}]"
        return f"[The latest accepted correction's routing header is {route}. Ignore older routing headers in the combined correction.]"
    return "[The latest accepted correction has no complete voice routing header. Ignore older routing headers and do not send audio.]"


def _with_runtime_note(message: Any) -> Any:
    if isinstance(message, str):
        return message + "\n\n" + _RUNTIME_NOTE
    if isinstance(message, list):
        return [*message, {"type": "text", "text": _RUNTIME_NOTE}]
    return message


def _argument_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _argument_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _argument_strings(item)


def _read_only_reference(name: str, arguments: dict) -> bool:
    if name != "terminal" or not isinstance(arguments.get("command"), str):
        return False
    command = arguments["command"]
    if re.search(r"[;&|<>\x60$\r\n]", command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return bool(words) and words[0].rsplit("/", 1)[-1] in {
        "cat", "head", "tail", "rg", "grep", "wc", "stat", "ls", "file", "readlink",
    }


class SwitchboardTurn:
    def __init__(self, agent, turn_id: str, delivery, previous_callback, *, input_generation=None):
        self.agent = agent
        self.turn_id = turn_id
        self.delivery = delivery
        self.previous_callback = previous_callback
        self.input_generation = input_generation if input_generation is not None else getattr(agent, "_switchboard_input_generation", None)
        self.writer = None
        self.closed = False
        self.abort_reason = None
        self.boundary = "pending"
        self.marker_pending = ""

    def is_current(self) -> bool:
        return (
            not self.closed
            and self.input_generation == getattr(self.agent, "_switchboard_input_generation", None)
            and getattr(self.agent, "_switchboard_turn", None) is self
            and (
                getattr(self.agent, "_current_turn_id", None) == self.turn_id
                or getattr(self.agent, "_relay_pending_turn_id", None) == self.turn_id
            )
        )

    def is_cancelled(self) -> bool:
        return (
            self.closed or self.abort_reason is not None
            or self.input_generation != getattr(self.agent, "_switchboard_input_generation", None)
            or bool(getattr(self.agent, "_interrupt_requested", False))
        )

    def on_delta(self, text: str) -> None:
        if not self.is_current() or not isinstance(text, str) or not text:
            return
        if self.is_cancelled():
            self.abort("interrupted")
            return
        superseded = getattr(self.agent, "_stream_writer_superseded", None)
        if superseded is not None and superseded():
            return
        writer = (
            getattr(self.agent, "_api_call_count", 0),
            getattr(self.agent, "_stream_writer_token", 0),
        )
        if self.writer is not None and writer != self.writer:
            # A provider retry may start at the beginning. Accepted speech cannot
            # be rewound, so reset only while no clause has been queued for Relay.
            self.delivery.reset_attempt()
            self._reset_boundary()
        self.writer = writer
        visible, spoken = self._split_delta(text)
        # The caller keeps terminal-only status updates; the protocol marker stays hidden.
        try:
            if visible and self.previous_callback is not None:
                self.previous_callback(visible)
        finally:
            if spoken:
                self.delivery.on_delta(spoken)

    def _split_delta(self, text: str) -> tuple[str, str]:
        if self.boundary == "spoken":
            return text, text
        if self.boundary == "unmarked":
            return text, ""
        self.marker_pending += text
        candidate = self.marker_pending.lstrip()
        if FINAL_MARKER.startswith(candidate) and len(self.marker_pending) <= 1024:
            return "", ""
        pending, self.marker_pending = self.marker_pending, ""
        if candidate.startswith(FINAL_MARKER):
            self.boundary = "spoken"
            visible = pending[:len(pending) - len(candidate)] + candidate[len(FINAL_MARKER):]
            return visible, visible
        self.boundary = "unmarked"
        return pending, ""

    def _reset_boundary(self) -> None:
        if self.marker_pending and self.previous_callback is not None:
            # A partial prefix that turned out not to be a speech boundary is terminal text.
            if self.marker_pending.strip() != FINAL_MARKER:
                self.previous_callback(self.marker_pending)
        self.marker_pending = ""
        self.boundary = "pending"

    @staticmethod
    def clean_final_text(text: str) -> str:
        if not isinstance(text, str):
            return text
        candidate = text.lstrip()
        if candidate.startswith(FINAL_MARKER):
            return text[:len(text) - len(candidate)] + candidate[len(FINAL_MARKER):]
        return text

    def before_tool(self, name: str, arguments: dict) -> str | None:
        if self.closed:
            return None
        # Ownership excludes the legacy helper even before the first delta.
        # The runtime alone decides whether definitive rejection permits fallback.
        if name in _EXECUTION_TOOLS and not _read_only_reference(name, arguments) and any(
            _LEGACY_SEND.search(value) for value in _argument_strings(arguments)
        ):
            return (
                "Switchboard native delivery already owns this beta turn. "
                "Do not run the reply helper or another delivery command. "
                "Write the spoken reply directly as your final answer; "
                "the runtime handles delivery and any safe fallback."
            )
        # Text preceding a discovered tool is commentary. Discard only before
        # a clause attempt; otherwise cancel rather than mix it with the final.
        self.delivery.reset_attempt()
        self.writer = None
        self._reset_boundary()
        return None

    def abort(self, reason: str = "cancelled") -> None:
        self.abort_reason = reason
        if self.delivery is not None:
            self.delivery.abort(reason)

    def finish(self, result: dict) -> dict:
        final_text = self.clean_final_text(str(result.get("final_response") or ""))
        result["final_response"] = final_text
        completed = result.get("completed") is True and result.get("failed") is not True
        interrupted = result.get("interrupted") is True or self.is_cancelled()
        self.delivery.finish(final_text, completed=completed, interrupted=interrupted)
        outcome = self.delivery.wait(timeout=15)
        if not outcome.get("settled"):
            self.abort("delivery_drain_timeout")
            outcome = self.delivery.wait(timeout=3.5)
        if completed and not interrupted and outcome.get("settled") and self.delivery.fallback_allowed:
            outcome = self.delivery.fallback(final_text)
        outcome["final_boundary"] = "marked" if self.boundary == "spoken" else "completed_text"
        result["switchboard_delivery"] = outcome
        if outcome.get("failed") or outcome.get("uncertain") or outcome.get("cancelled") or not outcome.get("settled", True):
            logger.warning("Switchboard beta delivery incomplete (turn=%s, outcome=%s)", self.turn_id, outcome)
        return outcome

    def close(self) -> None:
        self.closed = True
        if getattr(self.agent, "_switchboard_turn", None) is self:
            self.agent._switchboard_turn = None


def _native_enabled(agent) -> bool:
    return (
        os.environ.get("HERMES_SWITCHBOARD_STREAMING") == "1"
        and getattr(agent, "platform", "") != "subagent"
        and not getattr(agent, "_parent_session_id", None)
    )


def prepare_switchboard_turn(agent, user_message, persist_user_message, callback, turn_id, *, input_generation=None):
    """Return controller + API-local message/callback, or the original values."""
    clean_message = persist_user_message if persist_user_message is not None else user_message
    header = parse_beta_header(clean_message)
    if not _native_enabled(agent) or header is None:
        return None, user_message, persist_user_message, callback
    from agent.switchboard_stream import BetaClauseDelivery

    controller = SwitchboardTurn(agent, turn_id, None, callback, input_generation=input_generation)
    agent._switchboard_turn = controller
    stream_id = uuid.uuid4().hex
    try:
        controller.delivery = BetaClauseDelivery(
            agent_id=header["agent"], tts=header["tts"], stream_id=stream_id, message_id=stream_id,
            is_current=controller.is_current, is_cancelled=controller.is_cancelled,
        )
    except Exception:
        controller.close()
        raise
    return controller, _with_runtime_note(user_message), clean_message, controller.on_delta


def current_switchboard_turn(agent, turn_id):
    controller = getattr(agent, "_switchboard_turn", None)
    return controller if controller is not None and controller.turn_id == turn_id else None


def queue_switchboard_redirect(agent, text: str, *, kind: str = "redirect") -> None:
    """Cancel the previous utterance at acceptance; keep the latest raw routing header.

    Hermes merges queued corrections for context. Their first header is no longer the
    current destination, so route from this separate latest-input slot instead.
    """
    if not _native_enabled(agent):
        return
    controller = getattr(agent, "_switchboard_turn", None)
    if (controller is None and not getattr(agent, "_switchboard_pending_inputs", None)
            and not getattr(agent, "_switchboard_drained_inputs", None)
            and parse_beta_header(text) is None):
        return
    pending = vars(agent).setdefault("_switchboard_pending_inputs", {})
    # Unique tokens avoid a lost increment when redirect and steer are accepted
    # under their different queue locks. Publication is one atomic assignment.
    generation = uuid.uuid4().hex
    agent._switchboard_input_generation = generation
    pending[kind] = (text, generation)
    if controller is not None:
        controller.abort("redirected")
    # The old provider can still have a late callback before the replacement
    # request claims its writer. Fence that gap without claiming this UI thread.
    ensure_writer = getattr(agent, "_ensure_stream_writer_state", None)
    if ensure_writer is not None:
        ensure_writer()
        with getattr(agent, "_stream_writer_lock", None) or nullcontext():
            agent._stream_writer_token += 1


def take_switchboard_input(agent, text: str | None, *, kind: str) -> None:
    """Take routing metadata under the same lock as Hermes' correction queue."""
    pending = getattr(agent, "_switchboard_pending_inputs", None)
    latest = pending.pop(kind, None) if pending else None
    if latest is not None and text:
        drained = vars(agent).setdefault("_switchboard_drained_inputs", {})
        drained[kind] = (text, *latest)


def apply_switchboard_redirect(agent, text: str, api_content: str, *, kind: str = "redirect") -> str:
    """Rebind speech when Hermes applies a correction, without changing its clean text.

    A redirect is a new utterance within the same logical Hermes turn. It therefore
    needs fresh delivery identities, while captured callbacks for its predecessor
    must remain fenced. Transport cancellation drains before the next stream starts.
    """
    previous = getattr(agent, "_switchboard_turn", None)
    if previous is None and not _native_enabled(agent):
        return api_content
    drained = getattr(agent, "_switchboard_drained_inputs", None)
    snapshot = drained.get(kind) if drained else None
    if snapshot is not None and snapshot[0] != text:
        snapshot = None
    if previous is None and snapshot is None and parse_beta_header(text) is None:
        return api_content
    if kind == "steer" and snapshot is None:
        return api_content
    latest = snapshot[1] if snapshot is not None else text
    generation = snapshot[2] if snapshot is not None else getattr(agent, "_switchboard_input_generation", None)
    if snapshot is not None:
        drained.pop(kind)
    callback = previous.previous_callback if previous is not None else getattr(agent, "_stream_callback", None)
    turn_id = previous.turn_id if previous is not None else (
        getattr(agent, "_current_turn_id", None) or getattr(agent, "_relay_pending_turn_id", None)
    )
    if previous is not None:
        previous.abort("redirected")
        previous.delivery.wait(timeout=3.5)
        previous.close()
    # Provider setup flushes benign scrubber tails straight to the then-current
    # callbacks. Drop the interrupted writer's tails before rebinding the sink.
    for name in ("_stream_think_scrubber", "_stream_context_scrubber"):
        scrubber = getattr(agent, name, None)
        if scrubber is not None:
            scrubber.flush()
    agent._stream_callback = callback
    if not turn_id:
        return api_content
    controller, _, _, rebound = prepare_switchboard_turn(
        agent, latest, None, callback, turn_id, input_generation=generation,
    )
    if controller is not None:
        agent._stream_callback = rebound
        return _with_runtime_note(api_content) + "\n\n" + _latest_route_note(latest)
    if previous is not None or snapshot is not None:
        return api_content + "\n\n" + _RELEASE_NOTE + "\n\n" + _latest_route_note(latest)
    return api_content
