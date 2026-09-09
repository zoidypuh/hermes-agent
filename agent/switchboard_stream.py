"""Literal text clauses for the opt-in, beta-only Switchboard delivery edge.

No TTS/provider imports: the native text callback already removes reasoning, and
Relay owns speech generation. A timed-out POST may have been accepted, so this
adapter never retries it or permits a whole-answer replay afterward.
"""
from __future__ import annotations

from collections import deque
import http.client
import json
import re
import socket
import threading
from typing import Callable


_SQUARE_WRAPPERS = frozenset({
    "soft", "whisper", "loud", "emphasis", "slow", "fast", "higher-pitch",
    "lower-pitch", "build-intensity", "decrease-intensity", "sing-song",
    "singing", "laugh-speak",
})
_STANDALONE_ANGLE_TAGS = frozenset({"br", "break", "laugh", "breath", "sigh"})
_TAG = re.compile(r"\s*(/?)\s*([A-Za-z][\w:-]*)(?:\s+.*?)?\s*(/?)\s*\Z", re.DOTALL)
_IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class LiteralClauseBuffer:
    """Emit exact source slices only at whitespace outside tags and wrappers.

    Square effects are atomic and never spell-corrected. Known square delivery
    wrappers and named angle wrappers stay whole, including nested wrappers.
    Unclosed/malformed markup fails at final flush without being submitted. No
    size threshold overrides a wrapper; the hard limit fails instead of cutting.
    """

    def __init__(self, min_chars: int = 24, soft_limit: int = 180, max_chars: int = 131072):
        if not 1 <= min_chars <= soft_limit <= max_chars:
            raise ValueError("Invalid clause buffer limits")
        self.min_chars, self.soft_limit, self.max_chars = min_chars, soft_limit, max_chars
        self._buffer = ""
        self._position = 0
        self._tag_start = None
        self._tag_end = ""
        self._tag_quote = ""
        self._square_depth = 0
        self._wrappers = []
        self._last_content = ""
        self._has_spoken = self._tag_seen = self._effect_pending = self._markup_invalid = False
        self._finished = False

    def _tag_complete(self, raw: str) -> None:
        self._tag_seen = True
        match = _TAG.fullmatch(raw[1:-1])
        if match is None:
            if raw[0] == "[":
                self._effect_pending = True
            else:
                self._markup_invalid = True
            return
        closing, name, self_closing = match.groups()
        name = name.lower()
        kind = raw[0]
        wrapper = (kind == "[" and name in _SQUARE_WRAPPERS) or (
            kind == "<" and name not in _STANDALONE_ANGLE_TAGS
        )
        if not wrapper or self_closing:
            if kind == "[":
                self._effect_pending = True
            return
        key = (kind, name)
        if closing:
            if self._wrappers and self._wrappers[-1] == key:
                self._wrappers.pop()
            else:
                self._markup_invalid = True
        else:
            self._wrappers.append(key)

    def feed(self, delta: str) -> list[str]:
        if self._finished:
            raise RuntimeError("Clause buffer is closed")
        if not isinstance(delta, str):
            raise TypeError("Text deltas must be strings")
        if len(self._buffer) + len(delta) > self.max_chars:
            raise ValueError("Clause buffer exceeded its text limit")
        self._buffer += delta
        clauses = []
        while self._position < len(self._buffer):
            char = self._buffer[self._position]
            if self._tag_start is not None:
                if self._tag_end == "]" and char == "[":
                    self._square_depth += 1
                if self._tag_end == "]" and char == "]" and self._square_depth:
                    self._square_depth -= 1
                elif self._tag_quote:
                    if char == self._tag_quote:
                        self._tag_quote = ""
                elif self._tag_end == ">" and char in "\"'":
                    self._tag_quote = char
                elif char == self._tag_end:
                    self._tag_complete(self._buffer[self._tag_start:self._position + 1])
                    self._tag_start = None
            elif char in "[<":
                self._tag_start = self._position
                self._tag_end = "]" if char == "[" else ">"
                self._square_depth = 0
            elif char.isspace():
                length = self._position + 1
                boundary = bool(self._last_content) and self._last_content in ".!?…;:," and length >= self.min_chars
                if (not self._wrappers and not self._effect_pending and not self._markup_invalid
                        and self._has_spoken and (boundary or length >= self.soft_limit)):
                    clauses.append(self._buffer[:length])
                    self._buffer = self._buffer[length:]
                    self._position = 0
                    self._last_content = ""
                    self._has_spoken = self._tag_seen = False
                    continue
            elif char not in "\"'”’)]}":
                self._last_content = char
                self._has_spoken = True
                self._effect_pending = False
            self._position += 1
        return clauses

    def finish(self) -> list[str]:
        if self._finished:
            return []
        if (self._tag_start is not None or self._wrappers or self._markup_invalid
                or self._effect_pending or (self._tag_seen and not self._has_spoken)):
            raise ValueError("Incomplete or dangling delivery markup")
        self._finished = True
        tail, self._buffer = self._buffer, ""
        return [tail] if tail else []

    def abort(self) -> None:
        self._finished = True
        self._buffer = ""
        self._wrappers.clear()


class RelayHttpTransport:
    """A closeable, bounded HTTP connection to the explicit beta endpoint."""

    def __init__(self, *, host: str = "100.79.30.18", port: int = 8868, timeout: float = 3):
        self.host, self.port, self.timeout = host, port, timeout
        self._connection = None
        self._lock = threading.Lock()

    def _request(self, method: str, payload: dict | None = None, *, path="/api/assistant-stream") -> dict:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        with self._lock:
            self._connection = connection
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
            connection.request(method, path, data, {
                "Content-Type": "application/json; charset=utf-8",
            })
            response = connection.getresponse()
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("Relay response exceeded its limit")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("Relay returned invalid JSON")
            return result
        finally:
            connection.close()
            with self._lock:
                if self._connection is connection:
                    self._connection = None

    def capabilities(self) -> dict:
        return self._request("GET")

    def post(self, payload: dict) -> dict:
        return self._request("POST", payload)

    def one_shot(self, payload: dict) -> dict:
        return self._request("POST", payload, path="/api/assistant-reply")

    def close(self) -> None:
        with self._lock:
            connection = self._connection
        if connection is not None:
            if connection.sock is not None:
                try:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # A completed response can race cancellation.
            connection.close()


class BetaClauseDelivery:
    """Nonblocking native-delta consumer with one serial HTTP worker per turn."""

    def __init__(
        self, *, agent_id: str, stream_id: str, message_id: str, tts: str = "xai",
        is_current: Callable[[], bool] = lambda: True,
        is_cancelled: Callable[[], bool] = lambda: False,
        transport=None, max_queue: int = 256, max_text_chars: int = 131072,
    ):
        if agent_id not in {"mara", "vera"} or tts != "xai":
            raise ValueError("Native speech streaming requires a beta xAI agent")
        if not all(isinstance(value, str) and _IDENTITY.fullmatch(value) for value in (stream_id, message_id)):
            raise ValueError("Explicit stream and message identities are required")
        if max_queue < 1 or max_text_chars < 180:
            raise ValueError("Invalid delivery limits")
        self._identity = dict(protocol=1, channel="beta", agent_id=agent_id, tts=tts,
                              stream_id=stream_id, message_id=message_id)
        self._is_current, self._is_cancelled = is_current, is_cancelled
        self._transport = transport or RelayHttpTransport()
        self._max_queue, self._max_text_chars = max_queue, max_text_chars
        self._condition = threading.Condition(threading.RLock())
        self._buffer = LiteralClauseBuffer(max_chars=max_text_chars)
        self._queue = deque()
        self._observed = ""
        self._finished = self._cancelled = self._failed = False
        self._accepted = self._uncertain = self._fallback = False
        self._begin_attempted = self._clause_committed = False
        self._fallback_attempted = False
        self._completed = False
        self._state, self._reason = "waiting", ""
        self._sequence = 0
        self._settled = threading.Event()
        self._thread = threading.Thread(target=self._run, name="switchboard-beta-stream", daemon=True)
        self._thread.start()

    def outcome(self) -> dict:
        with self._condition:
            return dict(state=self._state, accepted=self._accepted, uncertain=self._uncertain,
                        fallback_allowed=self._fallback, suppress_one_shot=not self._fallback,
                        completed=self._completed, failed=self._failed, cancelled=self._cancelled,
                        settled=self._settled.is_set(), next_seq=self._sequence, reason=self._reason)

    accepted = property(lambda self: self.outcome()["accepted"])
    uncertain = property(lambda self: self.outcome()["uncertain"])
    fallback_allowed = property(lambda self: self.outcome()["fallback_allowed"])
    suppress_one_shot = property(lambda self: self.outcome()["suppress_one_shot"])
    completed = property(lambda self: self.outcome()["completed"])
    failed = property(lambda self: self.outcome()["failed"])
    cancelled = property(lambda self: self.outcome()["cancelled"])

    def _stop_locked(self, reason: str, *, failed: bool) -> None:
        self._failed = self._failed or failed
        self._cancelled = True
        self._state = "failed" if self._failed else "cancelled"
        self._reason = reason
        self._queue.clear()
        self._buffer.abort()
        self._condition.notify_all()
        self._transport.close()

    def abort(self, reason: str = "cancelled") -> None:
        with self._condition:
            if self._completed or self._cancelled:
                return
            self._stop_locked(reason, failed=False)
        self._transport.close()

    def _guard(self) -> bool:
        if not self._is_current() or self._is_cancelled():
            self.abort("interrupted_or_stale_turn")
            return False
        with self._condition:
            return not self._cancelled

    def _enqueue_locked(self, clauses: list[str]) -> None:
        if len(self._queue) + len(clauses) > self._max_queue:
            self._stop_locked("clause_queue_full", failed=True)
            return
        if clauses:
            self._clause_committed = True
            self._queue.extend(clauses)
            self._condition.notify_all()

    def on_delta(self, text: str) -> None:
        if not self._guard():
            return
        with self._condition:
            if self._finished or self._cancelled or self._settled.is_set():
                return
            if not isinstance(text, str) or len(self._observed) + len(text) > self._max_text_chars:
                self._stop_locked("invalid_or_excessive_text", failed=True)
                return
            self._observed += text
            try:
                self._enqueue_locked(self._buffer.feed(text))
            except (ValueError, TypeError):
                self._stop_locked("clause_buffer_failed", failed=True)

    def reset_attempt(self) -> bool:
        with self._condition:
            if self._cancelled or self._finished or self._settled.is_set():
                return False
            if self._clause_committed or self._begin_attempted:
                self._stop_locked("writer_changed_after_clause", failed=False)
                reset = False
            else:
                self._observed = ""
                self._buffer = LiteralClauseBuffer(max_chars=self._max_text_chars)
                reset = True
        if not reset:
            self._transport.close()
        return reset

    def finish(self, final_text: str, *, completed: bool = True, interrupted: bool = False) -> None:
        if interrupted or not completed:
            self.abort("turn_interrupted" if interrupted else "turn_failed")
            return
        if not self._guard():
            return
        with self._condition:
            if self._finished or self._cancelled or self._settled.is_set():
                return
            if not isinstance(final_text, str):
                self._stop_locked("final_text_changed", failed=True)
                return
            if final_text.startswith(self._observed):
                remainder = final_text[len(self._observed):]
            elif final_text == self._observed.strip():
                # Hermes strips the completed answer's outer whitespace. Keep
                # the original deltas; never normalize their interior or tags.
                remainder = ""
            elif final_text.startswith(self._observed.lstrip()):
                remainder = final_text[len(self._observed.lstrip()):]
            else:
                self._stop_locked("final_text_changed", failed=True)
                return
            self.on_delta(remainder)
            if self._cancelled:
                return
            self._finished = True
            try:
                self._enqueue_locked(self._buffer.finish())
            except ValueError:
                self._stop_locked("incomplete_delivery_markup", failed=True)
            self._condition.notify_all()

    def wait(self, timeout: float = 5) -> dict:
        self._settled.wait(timeout)
        return self.outcome()

    def fallback(self, final_text: str) -> dict:
        """One beta whole-answer attempt only after proven pre-stream rejection."""
        if not self._guard():
            return self.outcome()
        with self._condition:
            if not self._settled.is_set() or not self._fallback or self._fallback_attempted:
                return self.outcome()
            if not isinstance(final_text, str) or not final_text.strip() or len(final_text) > self._max_text_chars:
                self._fallback = False
                self._state, self._reason, self._failed = "failed", "invalid_fallback_text", True
                return self.outcome()
            try:
                # Unavailable capability can settle before the remaining model
                # deltas arrive. Validate the complete fallback independently.
                validator = LiteralClauseBuffer(max_chars=self._max_text_chars)
                validator.feed(final_text)
                validator.finish()
            except ValueError:
                self._fallback = False
                self._state, self._reason, self._failed = "failed", "invalid_fallback_markup", True
                return self.outcome()
            self._fallback_attempted = self._uncertain = True
            self._fallback = False
            self._state = "fallback_pending"
            self._settled.clear()
        try:
            result = self._transport.one_shot({"agent_id": self._identity["agent_id"],
                                              "message_id": self._identity["message_id"],
                                              "expected_tts": self._identity["tts"], "text": final_text})
            with self._condition:
                if result.get("accepted") is False:
                    self._uncertain = False
                elif result.get("accepted") is True:
                    self._accepted, self._uncertain = True, False
                if result.get("ok") is not True or result.get("status") not in {"queued", "held"}:
                    raise RuntimeError("Relay fallback failed")
                self._accepted, self._uncertain = True, False
                if not self._cancelled:
                    self._completed = True
                    self._state = "fallback_" + result["status"]
        except Exception:
            with self._condition:
                self._state, self._reason, self._failed = "failed", "fallback_failed_or_uncertain", True
        finally:
            self._settled.set()
        return self.outcome()

    def _post(self, action: str, **fields) -> dict:
        return self._transport.post({**self._identity, "action": action, **fields})

    def _begin(self) -> bool:
        try:
            caps = self._transport.capabilities()
        except Exception:
            caps = {}
        with self._condition:
            if self._cancelled:
                return False
            if any(key in caps and caps[key] != expected for key, expected in (
                    ("channel", "beta"), ("tts", self._identity["tts"]), ("protocol", 1))):
                self._stop_locked("capability_route_mismatch", failed=True)
                return False
            if not (caps.get("ok") is True and caps.get("protocol") == 1 and caps.get("ready") is True
                    and caps.get("channel") == "beta" and caps.get("tts") == self._identity["tts"]):
                self._fallback = True
                self._state, self._reason = "unavailable", "capability_unavailable"
                return False
            self._begin_attempted = self._uncertain = True
        result = self._post("begin")
        with self._condition:
            if result.get("accepted") is False:
                self._uncertain = False
                self._fallback = not self._cancelled
                self._state, self._reason = "rejected", "begin_rejected"
                return False
            if result.get("accepted") is True:
                self._accepted, self._uncertain = True, False
            if result.get("ok") is not True or not self._accepted or result.get("next_seq") != 0:
                raise RuntimeError("Relay begin failed")
            if not self._cancelled:
                self._state = "streaming"
        return self._guard()

    def _send_clause(self, text: str) -> None:
        result = self._post("chunk", seq=self._sequence, text=text)
        if result.get("ok") is not True or result.get("accepted") is not True or result.get("next_seq") != self._sequence + 1:
            raise RuntimeError("Relay chunk failed")
        with self._condition:
            self._sequence += 1

    def _finish_stream(self) -> None:
        result = self._post("end", seq=self._sequence)
        if result.get("ok") is not True or result.get("accepted") is not True or result.get("next_seq") != self._sequence:
            raise RuntimeError("Relay end failed")
        with self._condition:
            if not self._cancelled:
                self._completed = True
                self._state = "completed"

    def _run(self) -> None:
        begun = False
        try:
            while self._guard():
                with self._condition:
                    if not self._queue and not self._finished:
                        self._condition.wait(0.1)
                        continue
                    clause = self._queue.popleft() if self._queue else None
                if not self._guard():
                    return
                if clause is None:
                    if begun:
                        self._finish_stream()
                    else:
                        with self._condition:
                            self._state, self._completed = "completed", True
                    return
                if not begun:
                    if not self._begin():
                        return
                    begun = True
                if not self._guard():
                    return
                self._send_clause(clause)
        except Exception:
            with self._condition:
                self._stop_locked("delivery_failed_or_uncertain", failed=not self._cancelled)
        finally:
            with self._condition:
                cancel = self._begin_attempted and not self._completed and not self._fallback
            if cancel:
                try:
                    self._post("cancel", reason=self._reason or "cancelled")
                except Exception:
                    pass  # Cancellation is best effort; a POST is never replayed.
            self._transport.close()
            with self._condition:
                self._queue.clear()
                self._buffer.abort()
            self._settled.set()
