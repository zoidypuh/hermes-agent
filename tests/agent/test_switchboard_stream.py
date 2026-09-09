"""Literal chunking and actual loopback HTTP; never touches a running Relay."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from agent.switchboard_stream import BetaClauseDelivery, LiteralClauseBuffer, RelayHttpTransport, _SQUARE_WRAPPERS


@pytest.mark.parametrize("text", [
    "  [chuckleing] Grüß dich, Gismar.\n\n[gaging] A quirky little tag.  ",
    "[clear throat] <soft>First sentence. Second clause, still soft.</soft> Next outside. ",
    "<whisper><slow>Nested delivery, with punctuation. Keep it whole.</slow></whisper> Outside. ",
    "[soft]A soft sentence. [whisper]And another, inside.[/whisper][/soft] Done now.  ",
    '<custom tone="a>b">Literal custom wrapper. No cut.</custom> More text after it. ',
    "Literal <think>visible callback content</think> is preserved here. ",
])
def test_every_delta_partition_preserves_exact_text_and_complete_wrappers(text):
    for position in range(len(text) + 1):
        chunker = LiteralClauseBuffer()
        chunks = chunker.feed(text[:position]) + chunker.feed(text[position:]) + chunker.finish()
        assert "".join(chunks) == text
        for opener, closer in [("<soft>", "</soft>"), ("<whisper>", "</whisper>"),
                               ("[soft]", "[/soft]"), ("<custom tone=\"a>b\">", "</custom>")]:
            if opener in text and closer in text:
                assert any(opener in chunk and closer in chunk for chunk in chunks)
    chunker = LiteralClauseBuffer()
    chunks = [chunk for char in text for chunk in chunker.feed(char)] + chunker.finish()
    assert "".join(chunks) == text


def test_buffer_threshold_soft_limit_final_tail_and_closed_state():
    chunker = LiteralClauseBuffer(min_chars=12, soft_limit=24)
    assert chunker.feed("Too short, ") == []
    assert chunker.feed("but enough now, tail") == ["Too short, but enough now, "]
    assert chunker.finish() == ["tail"]
    assert chunker.finish() == []
    with pytest.raises(RuntimeError):
        chunker.feed("late")
    chunker = LiteralClauseBuffer(min_chars=8, soft_limit=12, max_chars=64)
    assert chunker.feed("a long phrase without punctuation ") == ["a long phrase ", "without punctuation "]
    chunker = LiteralClauseBuffer(min_chars=8, soft_limit=12, max_chars=32)
    with pytest.raises(ValueError):
        chunker.feed("<soft>" + "x" * 40)
    chunker.abort()
    assert chunker.finish() == []


class FakeTransport:
    def __init__(self, *, available=True, begin_result=None, fail_action=None, block_action=None):
        self.available, self.begin_result = available, begin_result
        self.fail_action, self.block_action = fail_action, block_action
        self.calls, self.fallback_calls = [], []
        self.blocked, self.closed = threading.Event(), threading.Event()

    def capabilities(self):
        return dict(ok=True, protocol=1, channel="beta", tts="xai", ready=self.available)

    def post(self, payload):
        self.calls.append(payload)
        action = payload["action"]
        if action == self.block_action:
            self.blocked.set()
            assert self.closed.wait(5)
            raise TimeoutError("Fixture interrupted")
        if action == self.fail_action:
            raise TimeoutError("Fixture uncertain acceptance")
        if action == "begin" and self.begin_result is not None:
            return self.begin_result
        next_seq = payload.get("seq", 0) + (1 if action == "chunk" else 0)
        return dict(ok=True, accepted=True, next_seq=next_seq)

    def one_shot(self, payload):
        self.fallback_calls.append(payload)
        if self.fail_action == "one_shot":
            raise TimeoutError("Fixture uncertain fallback")
        return dict(ok=True, status="queued")

    def close(self):
        self.closed.set()


def delivery(transport, **kwargs):
    return BetaClauseDelivery(agent_id="vera", stream_id="fixture-stream", message_id="fixture-message",
                              transport=transport, **kwargs)


def settled(item):
    result = item.wait(timeout=5)
    assert result["settled"], result
    return result


def test_real_http_delivers_exact_first_clause_before_finish_and_ordered_final_tail():
    records = []
    first_chunk = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            assert self.path == "/api/assistant-stream"
            self.respond(dict(ok=True, protocol=1, ready=True, channel="beta", tts="xai"))

        def do_POST(self):
            assert self.path == "/api/assistant-stream"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            records.append(payload)
            if payload["action"] == "chunk":
                first_chunk.set()
            next_seq = payload.get("seq", 0) + (1 if payload["action"] == "chunk" else 0)
            self.respond(dict(ok=True, accepted=True, next_seq=next_seq))

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        item = delivery(RelayHttpTransport(host="127.0.0.1", port=server.server_port))
        first = "[chuckleing] The first clause can arrive now, "
        tail = "<soft>Second sentence. Still wrapped.</soft> Grüß dich.  "
        item.on_delta(first)
        assert first_chunk.wait(5)
        assert all(record["action"] != "end" for record in records)
        item.on_delta(tail[:9])
        item.finish(first + tail)
        result = settled(item)
        assert result["completed"] and result["accepted"] and result["suppress_one_shot"]
        assert [record["action"] for record in records][0] == "begin"
        assert records[-1]["action"] == "end"
        clauses = [record for record in records if record["action"] == "chunk"]
        assert [record["seq"] for record in clauses] == list(range(len(clauses)))
        assert "".join(record["text"] for record in clauses) == first + tail
        assert records[-1]["seq"] == len(clauses)
        assert {record["message_id"] for record in records} == {"fixture-message"}
        before = len(records)
        item.on_delta("late")
        item.finish(first + tail)
        assert len(records) == before
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.parametrize("transport", [FakeTransport(available=False),
                                      FakeTransport(begin_result={"ok": False, "accepted": False})])
def test_only_definitive_nonacceptance_allows_one_literal_fallback(transport):
    item = delivery(transport)
    text = "  The full fallback keeps this exact whitespace.  "
    item.finish(text)
    result = settled(item)
    assert result["fallback_allowed"] and not result["suppress_one_shot"]
    result = item.fallback(text)
    assert result["completed"] and result["accepted"] and result["suppress_one_shot"]
    item.fallback(text)
    assert [call["text"] for call in transport.fallback_calls] == [text]
    assert transport.fallback_calls[0]["expected_tts"] == "xai"


@pytest.mark.parametrize("action", ["begin", "chunk", "end", "one_shot"])
def test_uncertain_posts_are_not_retried_or_replayed(action):
    transport = FakeTransport(available=action != "one_shot", fail_action=action)
    item = delivery(transport)
    text = "A complete sentence long enough for the first clause. "
    item.finish(text)
    result = settled(item)
    if action == "one_shot":
        result = item.fallback(text)
    assert result["failed"] and result["suppress_one_shot"] and not result["fallback_allowed"]
    item.fallback(text)
    assert sum(call["action"] == action for call in transport.calls) <= 1
    assert len(transport.fallback_calls) == (1 if action == "one_shot" else 0)


def test_abort_closes_inflight_transport_clears_clauses_and_ignores_late_deltas():
    transport = FakeTransport(block_action="chunk")
    item = delivery(transport)
    text = "The first chunk is waiting for its HTTP response. Another complete queued sentence. "
    item.on_delta(text)
    assert transport.blocked.wait(5)
    item.abort("fixture_barge_in")
    item.on_delta("This late result must not be sent. ")
    item.finish(text)
    result = settled(item)
    assert result["cancelled"] and result["suppress_one_shot"]
    assert [call["action"] for call in transport.calls] == ["begin", "chunk", "cancel"]


def test_pretool_reset_is_safe_only_before_any_clause_was_committed():
    transport = FakeTransport()
    item = delivery(transport)
    item.on_delta("A short draft")
    assert item.reset_attempt()
    final = "The actual final sentence has enough characters. "
    item.finish(final)
    assert settled(item)["completed"]
    assert "".join(call["text"] for call in transport.calls if call["action"] == "chunk") == final
    transport = FakeTransport(block_action="chunk")
    item = delivery(transport)
    item.on_delta(final)
    assert transport.blocked.wait(5)
    assert not item.reset_attempt()
    assert settled(item)["cancelled"]


@pytest.mark.parametrize("mode", ["mismatch", "queue_full", "stale", "interrupted"])
def test_changed_final_overload_and_stale_turns_stop_without_silent_loss(mode):
    transport = FakeTransport()
    state = {"current": True, "interrupted": False}
    item = delivery(transport, max_queue=1 if mode == "queue_full" else 256,
                    is_current=lambda: state["current"], is_cancelled=lambda: state["interrupted"])
    item.on_delta("tiny draft")
    if mode == "mismatch":
        item.finish("Completely changed final answer.")
    elif mode == "queue_full":
        item.on_delta(" " + "This sentence is long enough to emit. " * 4)
    else:
        state["current"] = mode != "stale"
        state["interrupted"] = mode == "interrupted"
        item.finish("tiny draft with a complete final sentence. ")
    result = settled(item)
    assert result["cancelled"] and not result["completed"] and not result["fallback_allowed"]
    assert not any(call["action"] == "end" for call in transport.calls)


def test_stale_turn_cannot_take_previously_available_fallback():
    transport = FakeTransport(available=False)
    state = {"current": True}
    item = delivery(transport, is_current=lambda: state["current"])
    item.finish("A final response to the old turn. ")
    assert settled(item)["fallback_allowed"]
    state["current"] = False
    result = item.fallback("A final response to the old turn. ")
    assert result["cancelled"]
    assert transport.fallback_calls == []


@pytest.mark.parametrize("wrapper", sorted(_SQUARE_WRAPPERS))
def test_submitted_wrapper_is_one_complete_unit_even_with_token_sized_deltas(wrapper):
    transport = FakeTransport()
    item = delivery(transport)
    phrase = f"<{wrapper}>A complete sentence inside a wrapper. Another clause, still inside.</{wrapper}>"
    for char in phrase[:-1]:
        item.on_delta(char)
    assert transport.calls == []
    item.on_delta(phrase[-1])
    item.finish(phrase)
    assert settled(item)["completed"]
    assert [call["text"] for call in transport.calls if call["action"] == "chunk"] == [phrase]


@pytest.mark.parametrize("text", ["<whisper>A complete sentence here.", "<soft></soft>",
                                 "[kiss]", "[chuckl", "<soft>Text.</whisper>",
                                 "[soft]Text.[/whisper]"])
def test_final_malformed_wrappers_or_dangling_effects_never_submit_or_fallback(text):
    transport = FakeTransport()
    item = delivery(transport)
    item.on_delta(text)
    item.finish(text)
    result = settled(item)
    assert result["failed"] and result["cancelled"] and not result["fallback_allowed"]
    assert transport.calls == []


def test_inline_effect_attaches_to_following_text_and_bad_tail_cancels_accepted_stream():
    transport = FakeTransport()
    item = delivery(transport)
    item.on_delta("[kiss] ")
    assert transport.calls == []
    phrase = "[kiss] The following spoken sentence is long enough. "
    item.finish(phrase)
    assert settled(item)["completed"]
    assert [call["text"] for call in transport.calls if call["action"] == "chunk"] == [phrase]
    transport = FakeTransport(block_action="chunk")
    item = delivery(transport)
    item.on_delta("An earlier sentence has already been submitted. ")
    assert transport.blocked.wait(5)
    item.on_delta("<whisper>This delivery wrapper never closes.")
    item.finish("An earlier sentence has already been submitted. <whisper>This delivery wrapper never closes.")
    result = settled(item)
    assert result["failed"] and result["accepted"] and not result["fallback_allowed"]
    assert [call["action"] for call in transport.calls] == ["begin", "chunk", "cancel"]


@pytest.mark.parametrize("key,value", [("tts", "mac"), ("channel", "stable"), ("protocol", 2)])
def test_explicit_capability_route_mismatch_never_falls_back(key, value):
    transport = FakeTransport()
    capabilities = transport.capabilities()
    capabilities[key] = value
    transport.capabilities = lambda: capabilities
    item = delivery(transport)
    item.finish("A complete speech request with the original xAI tags. ")
    result = settled(item)
    assert result["failed"] and not result["fallback_allowed"]
    assert transport.calls == []


@pytest.mark.parametrize("observed,final,expected", [
    ("  [kiss] A complete sentence with spaces.  \n", "[kiss] A complete sentence with spaces.",
     "  [kiss] A complete sentence with spaces.  \n"),
    ("\n\nA partial", "A partial final answer.", "\n\nA partial final answer."),
])
def test_production_outer_whitespace_strip_keeps_original_deltas_literal(observed, final, expected):
    transport = FakeTransport()
    item = delivery(transport)
    item.on_delta(observed)
    item.finish(final)
    assert settled(item)["completed"]
    assert "".join(call["text"] for call in transport.calls if call["action"] == "chunk") == expected


def test_fallback_validates_late_markup_after_early_capability_rejection():
    transport = FakeTransport(available=False)
    item = delivery(transport)
    first = "The initial complete sentence can leave the buffer. "
    item.on_delta(first)
    assert settled(item)["fallback_allowed"]
    final = first + "<whisper>This late wrapper never closes."
    item.on_delta(final[len(first):])
    item.finish(final)
    result = item.fallback(final)
    assert result["failed"] and not result["fallback_allowed"]
    assert transport.fallback_calls == []
