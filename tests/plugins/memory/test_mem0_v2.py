"""Tests for Mem0 API v2 compatibility — filters param and dict response unwrapping.

Salvaged from PRs #5301 (qaqcvc) and #5117 (vvvanguards).
"""

import json
import pytest

from plugins.memory.mem0 import (
    Mem0MemoryProvider,
    _LocalMem0Client,
    _load_config,
    _strict_memory_text_allowed,
)


class FakeClientV2:
    """Fake Mem0 client that returns v2-style dict responses and captures call kwargs."""

    def __init__(self, search_results=None, all_results=None):
        self._search_results = search_results or {"results": []}
        self._all_results = all_results or {"results": []}
        self.captured_search = {}
        self.captured_searches = []
        self.captured_get_all = {}
        self.captured_get_alls = []
        self.captured_add = []

    def search(self, **kwargs):
        self.captured_search = kwargs
        self.captured_searches.append(kwargs)
        if callable(self._search_results):
            return self._search_results(kwargs)
        return self._search_results

    def get_all(self, **kwargs):
        self.captured_get_all = kwargs
        self.captured_get_alls.append(kwargs)
        if callable(self._all_results):
            return self._all_results(kwargs)
        return self._all_results

    def add(self, messages, **kwargs):
        self.captured_add.append({"messages": messages, **kwargs})


class TestStrictMemoryTextAllowed:
    def test_rejects_operational_sensitive_media_residue(self):
        assert _strict_memory_text_allowed(
            "Do not store or inject folder paths, filenames, project progress, "
            "or content descriptions for sensitive or explicit image/video generation workflows."
        ) is False

    def test_rejects_real_person_media_workflow_progress(self):
        assert _strict_memory_text_allowed(
            "Gismar's explicit real-person media workflow is in a local folder "
            "and the next task is to resume asset sorting."
        ) is False

    def test_allows_sanitized_grok_moderation_anchor(self):
        assert _strict_memory_text_allowed(
            "Gismar felt foolish for bragging to Grok about bypassing its image/video moderation."
        ) is True

    def test_allows_sanitized_venice_provider_anchor(self):
        assert _strict_memory_text_allowed(
            "Gismar learned that Venice hosts Grok image generation and believes it appears much less restricted than Grok's own private mode."
        ) is True

    def test_allows_non_sensitive_image_workflow_anchor(self):
        assert _strict_memory_text_allowed(
            "For Gismar's Comfy media workflow, OpenAI GPT Image endpoints are a viable image-only route."
        ) is True

    def test_explicitly_does_not_trigger_sensitive_media_guard(self):
        assert _strict_memory_text_allowed(
            "Memory policy for Mara: Mara may save useful durable facts without requiring Gismar to explicitly say save this."
        ) is True


# ---------------------------------------------------------------------------
# Filter migration: bare user_id= -> filters={}
# ---------------------------------------------------------------------------


class TestMem0FiltersV2:
    """All API calls must use filters={} instead of bare user_id= kwargs."""

    def _make_provider(self, monkeypatch, client):
        provider = Mem0MemoryProvider()
        provider.initialize("test-session")
        provider._user_id = "u123"
        provider._read_user_ids = ["u123"]
        provider._agent_id = "hermes"
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        return provider

    def test_search_uses_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.handle_tool_call("mem0_search", {"query": "hello", "top_k": 3, "rerank": False})

        assert client.captured_search["query"] == "hello"
        assert client.captured_search["top_k"] == 3
        assert client.captured_search["rerank"] is False
        assert client.captured_search["filters"] == {"user_id": "u123"}
        # Must NOT have bare user_id kwarg
        assert "user_id" not in {k for k in client.captured_search if k != "filters"}

    def test_profile_uses_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.handle_tool_call("mem0_profile", {})

        assert client.captured_get_all["filters"] == {"user_id": "u123"}
        assert "user_id" not in {k for k in client.captured_get_all if k != "filters"}

    def test_prefetch_uses_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.queue_prefetch("hello")
        provider._prefetch_thread.join(timeout=2)

        assert client.captured_search["query"] == "hello"
        assert client.captured_search["filters"] == {"user_id": "u123"}
        assert "user_id" not in {k for k in client.captured_search if k != "filters"}

    def test_prefetch_uses_configured_threshold_and_top_k(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [
                {"id": "drop-low", "memory": "low score memory", "score": 0.69},
                {"id": "keep", "memory": "high score memory", "score": 0.8},
                {"id": "drop-missing", "memory": "missing score memory"},
            ]
        })
        provider = self._make_provider(monkeypatch, client)
        provider._similarity_threshold = 0.7
        provider._prefetch_top_k = 3

        provider.queue_prefetch("hello")
        provider._prefetch_thread.join(timeout=2)
        result = provider.prefetch("hello")

        assert client.captured_search["top_k"] == 3
        assert client.captured_search["threshold"] == 0.7
        assert "high score memory" in result
        assert "low score memory" not in result
        assert "missing score memory" not in result

    def test_prefetch_empty_search_clears_stale_context(self, monkeypatch):
        client = FakeClientV2(search_results={"results": []})
        provider = self._make_provider(monkeypatch, client)
        provider._similarity_threshold = 0.7
        provider._prefetch_result = "- stale memory"

        provider.queue_prefetch("hello")
        provider._prefetch_thread.join(timeout=2)

        assert provider.prefetch("hello") == ""

    def test_auto_inject_disabled_skips_prefetch_search_and_clears_context(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [{"id": "keep", "memory": "should not inject", "score": 0.9}]
        })
        provider = self._make_provider(monkeypatch, client)
        provider._auto_inject_enabled = False
        provider._prefetch_result = "- stale memory"

        provider.queue_prefetch("hello")

        assert client.captured_searches == []
        assert provider.prefetch("hello") == ""

    def test_debug_inject_scores_includes_scores_in_prefetch(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [{
                "id": "keep",
                "memory": "scored memory",
                "score": 0.8,
                "rerank_score": 0.123456,
            }]
        })
        provider = self._make_provider(monkeypatch, client)
        provider._debug_inject_scores = True

        provider.queue_prefetch("hello")
        provider._prefetch_thread.join(timeout=2)
        result = provider.prefetch("hello")

        assert "[rerank=0.1235, vector=0.8000]" in result
        assert "scored memory" in result

    def test_auto_add_disabled_skips_sync_turn(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._auto_add_enabled = False
        provider._inference_enabled = True
        provider._strict_filter = True

        provider.sync_turn("save nothing automatically", "ok", session_id="s1")

        assert provider._sync_thread is None
        assert client.captured_add == []

    def test_write_disabled_skips_sync_turn_and_hides_conclude_tool(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._write_enabled = False
        provider._auto_add_enabled = True

        provider.sync_turn("save nothing", "ok", session_id="s1")

        assert provider._sync_thread is None
        assert client.captured_add == []
        assert "mem0_conclude" not in {schema["name"] for schema in provider.get_tool_schemas()}

    def test_write_disabled_blocks_conclude(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._write_enabled = False

        response = provider.handle_tool_call("mem0_conclude", {"conclusion": "user likes dark mode"})

        assert "disabled" in response
        assert client.captured_add == []

    def test_search_uses_configured_threshold_and_default_top_k(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [
                {"id": "drop", "memory": "below threshold", "score": 0.4},
                {"id": "keep", "memory": "above threshold", "score": 0.9},
            ]
        })
        provider = self._make_provider(monkeypatch, client)
        provider._similarity_threshold = 0.7
        provider._search_top_k = 3

        response = json.loads(provider.handle_tool_call("mem0_search", {"query": "memory"}))

        assert client.captured_search["top_k"] == 3
        assert client.captured_search["threshold"] == 0.7
        assert response["count"] == 1
        assert response["results"][0]["memory"] == "above threshold"

    def test_search_reads_multiple_user_ids_and_merges(self, monkeypatch):
        def search_results(kwargs):
            user_id = kwargs["filters"]["user_id"]
            results = {
                "mara_high": [{"id": "mara", "memory": "Mara high memory", "score": 0.8}],
                "gismar_restore_brain": [{"id": "old", "memory": "Old restored memory", "score": 0.95}],
                "cloud_import": [{"id": "cloud", "memory": "Cloud imported memory", "score": 0.7}],
            }
            return {"results": results[user_id]}

        client = FakeClientV2(search_results=search_results)
        provider = self._make_provider(monkeypatch, client)
        provider._user_id = "mara_high"
        provider._read_user_ids = ["mara_high", "gismar_restore_brain", "cloud_import"]

        response = json.loads(provider.handle_tool_call("mem0_search", {"query": "memory", "top_k": 2}))

        assert [call["filters"]["user_id"] for call in client.captured_searches] == [
            "mara_high",
            "gismar_restore_brain",
            "cloud_import",
        ]
        assert response["count"] == 2
        assert [item["memory"] for item in response["results"]] == [
            "Old restored memory",
            "Mara high memory",
        ]

    def test_search_merge_preserves_rerank_order(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [
                {"id": "best", "memory": "best reranked memory", "score": 0.5, "rerank_score": 0.9},
                {"id": "worse", "memory": "worse vector memory", "score": 0.99, "rerank_score": 0.1},
            ]
        })
        provider = self._make_provider(monkeypatch, client)

        response = json.loads(provider.handle_tool_call("mem0_search", {"query": "memory", "rerank": True}))

        assert response["results"][0]["memory"] == "best reranked memory"
        assert response["results"][0]["rerank_score"] == 0.9

    def test_search_applies_rerank_threshold_when_available(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [
                {"id": "keep", "memory": "high rerank memory", "score": 0.5, "rerank_score": 0.2},
                {"id": "drop", "memory": "low rerank memory", "score": 0.99, "rerank_score": 0.01},
            ]
        })
        provider = self._make_provider(monkeypatch, client)
        provider._rerank_threshold = 0.05

        response = json.loads(provider.handle_tool_call("mem0_search", {"query": "memory", "rerank": True}))

        assert response["count"] == 1
        assert response["results"][0]["memory"] == "high rerank memory"

    def test_search_reads_candidate_user_id_with_stricter_rerank_threshold_when_enabled(self, monkeypatch):
        def search_results(kwargs):
            user_id = kwargs["filters"]["user_id"]
            if user_id == "candidate":
                return {"results": [
                    {"id": "candidate-low", "memory": "candidate low", "score": 0.99, "rerank_score": 0.2},
                    {"id": "candidate-high", "memory": "candidate high", "score": 0.5, "rerank_score": 0.35},
                ]}
            return {"results": [{"id": "trusted", "memory": "trusted low", "score": 0.4, "rerank_score": 0.1}]}

        client = FakeClientV2(search_results=search_results)
        provider = self._make_provider(monkeypatch, client)
        provider._user_id = "trusted"
        provider._read_user_ids = ["trusted"]
        provider._candidate_user_id = "candidate"
        provider._candidate_read_enabled = True
        provider._rerank_threshold = 0.05
        provider._candidate_rerank_threshold = 0.30

        response = json.loads(provider.handle_tool_call("mem0_search", {"query": "memory", "rerank": True}))

        assert [call["filters"]["user_id"] for call in client.captured_searches] == ["trusted", "candidate"]
        assert response["count"] == 2
        assert [item["memory"] for item in response["results"]] == ["candidate high", "trusted low"]
        assert "candidate low" not in json.dumps(response)

    def test_search_does_not_read_candidate_user_id_by_default(self, monkeypatch):
        client = FakeClientV2(search_results={"results": [{"id": "trusted", "memory": "trusted", "score": 0.9}]})
        provider = self._make_provider(monkeypatch, client)
        provider._user_id = "trusted"
        provider._read_user_ids = ["trusted"]
        provider._candidate_user_id = "candidate"

        response = json.loads(provider.handle_tool_call("mem0_search", {"query": "memory", "rerank": True}))

        assert [call["filters"]["user_id"] for call in client.captured_searches] == ["trusted"]
        assert response["count"] == 1

    def test_profile_reads_multiple_user_ids(self, monkeypatch):
        def all_results(kwargs):
            user_id = kwargs["filters"]["user_id"]
            return {"results": [{"id": user_id, "memory": f"{user_id} memory"}]}

        client = FakeClientV2(all_results=all_results)
        provider = self._make_provider(monkeypatch, client)
        provider._user_id = "mara_high"
        provider._read_user_ids = ["mara_high", "cloud_import"]

        response = json.loads(provider.handle_tool_call("mem0_profile", {}))

        assert [call["filters"]["user_id"] for call in client.captured_get_alls] == [
            "mara_high",
            "cloud_import",
        ]
        assert response["count"] == 2

    def test_profile_does_not_dump_candidate_user_id_by_default(self, monkeypatch):
        def all_results(kwargs):
            user_id = kwargs["filters"]["user_id"]
            return {"results": [{"id": user_id, "memory": f"{user_id} memory"}]}

        client = FakeClientV2(all_results=all_results)
        provider = self._make_provider(monkeypatch, client)
        provider._user_id = "trusted"
        provider._read_user_ids = ["trusted"]
        provider._candidate_user_id = "candidate"

        response = json.loads(provider.handle_tool_call("mem0_profile", {}))

        assert [call["filters"]["user_id"] for call in client.captured_get_alls] == ["trusted"]
        assert response["count"] == 1
        assert "candidate memory" not in response["result"]

    def test_sync_turn_uses_write_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._inference_enabled = True

        provider.sync_turn("user said this", "assistant replied", session_id="s1")
        provider._sync_thread.join(timeout=2)

        assert len(client.captured_add) == 1
        call = client.captured_add[0]
        assert call["user_id"] == "u123"
        assert call["agent_id"] == "hermes"
        assert call["metadata"]["write_origin"] == "mem0_inference"
        assert call["metadata"]["source_session_id"] == "s1"
        assert call["metadata"]["source_user_turn"] == "user said this"
        assert call["metadata"]["source_assistant_turn"] == "assistant replied"

    def test_sync_turn_strict_filter_writes_exact_memories_with_infer_false(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._inference_enabled = False
        provider._strict_filter = True
        monkeypatch.setattr(
            provider,
            "_extract_strict_memory_decision",
            lambda user, assistant: {
                "memories": ["User prefers high-signal memories only."],
                "raw_memories": ["User prefers high-signal memories only."],
                "reject_reason": "",
            },
        )

        provider.sync_turn("please remember high signal only", "ok", session_id="s1")
        provider._sync_thread.join(timeout=2)

        assert len(client.captured_add) == 1
        call = client.captured_add[0]
        assert call["messages"] == [
            {"role": "user", "content": "User prefers high-signal memories only."}
        ]
        assert call["user_id"] == "u123"
        assert call["agent_id"] == "hermes"
        assert call["infer"] is False
        assert call["metadata"]["write_origin"] == "strict_turn_filter"
        assert call["metadata"]["source_session_id"] == "s1"
        assert call["metadata"]["source_user_turn"] == "please remember high signal only"
        assert call["metadata"]["source_assistant_turn"] == "ok"

    def test_sync_turn_strict_filter_writes_to_candidate_user_id(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._candidate_user_id = "candidate"
        provider._inference_enabled = False
        provider._strict_filter = True
        monkeypatch.setattr(
            provider,
            "_extract_strict_memory_decision",
            lambda user, assistant: {
                "memories": ["User prefers clean memory."],
                "raw_memories": ["User prefers clean memory."],
                "reject_reason": "",
            },
        )

        provider.sync_turn("please remember clean memory", "ok", session_id="s1")
        provider._sync_thread.join(timeout=2)

        call = client.captured_add[0]
        assert call["user_id"] == "candidate"
        assert call["agent_id"] == "hermes"
        assert call["infer"] is False
        assert call["metadata"]["source_user_turn"] == "please remember clean memory"
        assert call["metadata"]["source_assistant_turn"] == "ok"

    def test_sync_turn_metadata_truncates_source_turns(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._inference_enabled = True
        long_user = "u" * 5000
        long_assistant = "a" * 5001

        provider.sync_turn(long_user, long_assistant, session_id="s1")
        provider._sync_thread.join(timeout=2)

        metadata = client.captured_add[0]["metadata"]
        assert len(metadata["source_user_turn"]) == 4000
        assert len(metadata["source_assistant_turn"]) == 4000
        assert metadata["source_user_turn_truncated"] is True
        assert metadata["source_assistant_turn_truncated"] is True
        assert metadata["source_user_turn_original_chars"] == 5000
        assert metadata["source_assistant_turn_original_chars"] == 5001

    def test_sync_turn_strict_filter_skips_when_no_memory(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._inference_enabled = False
        provider._strict_filter = True
        monkeypatch.setattr(
            provider,
            "_extract_strict_memory_decision",
            lambda user, assistant: {"memories": [], "raw_memories": [], "reject_reason": "no_anchor_memory"},
        )

        provider.sync_turn("ok thanks lol", "sure", session_id="s1")
        provider._sync_thread.join(timeout=2)

        assert client.captured_add == []

    def test_sync_turn_strict_filter_audits_accepted_decision(self, monkeypatch, tmp_path):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._audit_log_path = str(tmp_path / "audit.jsonl")
        provider._inference_enabled = False
        provider._strict_filter = True
        monkeypatch.setattr(
            provider,
            "_extract_strict_memory_decision",
            lambda user, assistant: {
                "memories": ["User likes concise anchor memories."],
                "raw_memories": ["User likes concise anchor memories."],
                "reject_reason": "",
            },
        )

        provider.sync_turn("please remember concise anchors", "ok", session_id="s1")
        provider._sync_thread.join(timeout=2)

        record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip())
        assert record["session_id"] == "s1"
        assert record["accepted"] is True
        assert record["memories"] == ["User likes concise anchor memories."]
        assert record["stored_memories"] == ["User likes concise anchor memories."]
        assert record["write_user_id"] == "u123"
        assert record["user"]["text"] == "please remember concise anchors"
        assert record["assistant"]["text"] == "ok"

    def test_sync_turn_strict_filter_audits_rejected_decision(self, monkeypatch, tmp_path):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._audit_log_path = str(tmp_path / "audit.jsonl")
        provider._inference_enabled = False
        provider._strict_filter = True
        monkeypatch.setattr(
            provider,
            "_extract_strict_memory_decision",
            lambda user, assistant: {"memories": [], "raw_memories": [], "reject_reason": "no_anchor_memory"},
        )

        provider.sync_turn("ok thanks lol", "sure", session_id="s1")
        provider._sync_thread.join(timeout=2)

        record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip())
        assert record["accepted"] is False
        assert record["reject_reason"] == "no_anchor_memory"
        assert record["memories"] == []
        assert client.captured_add == []

    def test_sync_turn_strict_filter_skips_voice_turn_before_llm(self, monkeypatch, tmp_path):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)
        provider._audit_log_path = str(tmp_path / "audit.jsonl")
        provider._inference_enabled = False
        provider._strict_filter = True

        def fail_extract(user, assistant):
            raise AssertionError("voice turns should be skipped before LLM extraction")

        monkeypatch.setattr(provider, "_extract_strict_memory_decision", fail_extract)

        provider.sync_turn("[V] messy voice transcription", "ok", session_id="s1")

        assert provider._sync_thread is None
        assert client.captured_add == []
        record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip())
        assert record["accepted"] is False
        assert record["reject_reason"] == "voice_turn"

    def test_conclude_uses_write_filters(self, monkeypatch):
        client = FakeClientV2()
        provider = self._make_provider(monkeypatch, client)

        provider.handle_tool_call("mem0_conclude", {"conclusion": "user likes dark mode"})

        assert len(client.captured_add) == 1
        call = client.captured_add[0]
        assert call["user_id"] == "u123"
        assert call["agent_id"] == "hermes"
        assert call["infer"] is False

    def test_read_filters_no_agent_id(self):
        """Read filters should use user_id only — cross-session recall across agents."""
        provider = Mem0MemoryProvider()
        provider._user_id = "u123"
        provider._agent_id = "hermes"
        assert provider._read_filters() == {"user_id": "u123"}

    def test_read_filter_sets_use_configured_scope(self):
        provider = Mem0MemoryProvider()
        provider._user_id = "mara_high"
        provider._read_user_ids = ["mara_high", "gismar_restore_brain", "cloud_import"]
        assert provider._read_filter_sets() == [
            {"user_id": "mara_high"},
            {"user_id": "gismar_restore_brain"},
            {"user_id": "cloud_import"},
        ]

    def test_read_filter_sets_include_candidate_scope_once(self):
        provider = Mem0MemoryProvider()
        provider._user_id = "mara_high"
        provider._read_user_ids = ["mara_high"]
        provider._candidate_user_id = "mara_candidate"
        provider._candidate_read_enabled = True

        assert provider._read_filter_sets() == [{"user_id": "mara_high"}, {"user_id": "mara_candidate"}]

    def test_read_filter_sets_exclude_candidate_scope_by_default(self):
        provider = Mem0MemoryProvider()
        provider._user_id = "mara_high"
        provider._read_user_ids = ["mara_high"]
        provider._candidate_user_id = "mara_candidate"

        assert provider._read_filter_sets() == [{"user_id": "mara_high"}]

    def test_write_filters_include_agent_id(self):
        """Write filters should include agent_id for attribution."""
        provider = Mem0MemoryProvider()
        provider._user_id = "u123"
        provider._agent_id = "hermes"
        assert provider._write_filters() == {"user_id": "u123", "agent_id": "hermes"}


# ---------------------------------------------------------------------------
# Dict response unwrapping (API v2 wraps in {"results": [...]})
# ---------------------------------------------------------------------------


class TestMem0ResponseUnwrapping:
    """API v2 returns {"results": [...]} dicts; we must extract the list."""

    def _make_provider(self, monkeypatch, client):
        provider = Mem0MemoryProvider()
        provider.initialize("test-session")
        monkeypatch.setattr(provider, "_get_client", lambda: client)
        return provider

    def test_profile_dict_response(self, monkeypatch):
        client = FakeClientV2(all_results={"results": [{"memory": "alpha"}, {"memory": "beta"}]})
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call("mem0_profile", {}))

        assert result["count"] == 2
        assert "alpha" in result["result"]
        assert "beta" in result["result"]

    def test_profile_list_response_backward_compat(self, monkeypatch):
        """Old API returned bare lists — still works."""
        client = FakeClientV2(all_results=[{"memory": "gamma"}])
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call("mem0_profile", {}))
        assert result["count"] == 1
        assert "gamma" in result["result"]

    def test_search_dict_response(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [{"memory": "foo", "score": 0.9}, {"memory": "bar", "score": 0.7}]
        })
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call(
            "mem0_search", {"query": "test", "top_k": 5}
        ))

        assert result["count"] == 2
        assert result["results"][0]["memory"] == "foo"

    def test_search_list_response_backward_compat(self, monkeypatch):
        """Old API returned bare lists — still works."""
        client = FakeClientV2(search_results=[{"memory": "baz", "score": 0.8}])
        provider = self._make_provider(monkeypatch, client)

        result = json.loads(provider.handle_tool_call(
            "mem0_search", {"query": "test"}
        ))
        assert result["count"] == 1

    def test_unwrap_results_edge_cases(self):
        """_unwrap_results handles all shapes gracefully."""
        assert Mem0MemoryProvider._unwrap_results({"results": [1, 2]}) == [1, 2]
        assert Mem0MemoryProvider._unwrap_results([3, 4]) == [3, 4]
        assert Mem0MemoryProvider._unwrap_results({}) == []
        assert Mem0MemoryProvider._unwrap_results(None) == []
        assert Mem0MemoryProvider._unwrap_results("unexpected") == []

    def test_prefetch_dict_response(self, monkeypatch):
        client = FakeClientV2(search_results={
            "results": [{"memory": "user prefers dark mode"}]
        })
        provider = Mem0MemoryProvider()
        provider.initialize("test-session")
        monkeypatch.setattr(provider, "_get_client", lambda: client)

        provider.queue_prefetch("preferences")
        provider._prefetch_thread.join(timeout=2)
        result = provider.prefetch("preferences")

        assert "dark mode" in result


# ---------------------------------------------------------------------------
# Default preservation
# ---------------------------------------------------------------------------


class TestMem0Defaults:
    """Ensure we don't break existing users' defaults."""

    def test_default_user_id_hermes_user(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "test-key")
        monkeypatch.delenv("MEM0_USER_ID", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        provider = Mem0MemoryProvider()
        provider.initialize("test")

        assert provider._user_id == "hermes-user"

    def test_default_agent_id_hermes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "test-key")
        monkeypatch.delenv("MEM0_AGENT_ID", raising=False)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        provider = Mem0MemoryProvider()
        provider.initialize("test")

        assert provider._agent_id == "hermes"

    def test_mem0_json_parses_threshold_and_top_k(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        (tmp_path / "mem0.json").write_text(json.dumps({
            "api_key": "test-key",
            "auto_add_enabled": "false",
            "auto_inject_enabled": "false",
            "audit_log_enabled": "false",
            "audit_log_path": str(tmp_path / "mem0-audit.jsonl"),
            "write_enabled": "false",
            "debug_inject_scores": "true",
            "candidate_user_id": "mara_candidate",
            "candidate_read_enabled": "true",
            "similarity_threshold": "0.7",
            "rerank_threshold": "0.05",
            "candidate_similarity_threshold": "0.8",
            "candidate_rerank_threshold": "0.3",
            "prefetch_top_k": "3",
            "search_top_k": "4",
        }))

        provider = Mem0MemoryProvider()
        provider.initialize("test")

        assert provider._similarity_threshold == 0.7
        assert provider._prefetch_top_k == 3
        assert provider._search_top_k == 4
        assert provider._auto_add_enabled is False
        assert provider._auto_inject_enabled is False
        assert provider._audit_log_enabled is False
        assert provider._audit_log_path == str(tmp_path / "mem0-audit.jsonl")
        assert provider._write_enabled is False
        assert provider._debug_inject_scores is True
        assert provider._rerank_threshold == 0.05
        assert provider._candidate_user_id == "mara_candidate"
        assert provider._candidate_read_enabled is True
        assert provider._candidate_similarity_threshold == 0.8
        assert provider._candidate_rerank_threshold == 0.3


class TestMem0LocalEndpoint:
    """Self-hosted Mem0 endpoint support."""

    def test_env_base_url_auto_selects_local_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("MEM0_BASE_URL", "http://127.0.0.1:8888")
        monkeypatch.delenv("MEM0_MODE", raising=False)
        monkeypatch.delenv("MEM0_API_KEY", raising=False)

        cfg = _load_config()
        provider = Mem0MemoryProvider()
        provider.initialize("test")

        assert cfg["base_url"] == "http://127.0.0.1:8888"
        assert provider._mode == "local"
        assert provider.is_available()

    def test_setup_schema_allows_local_without_api_key(self):
        provider = Mem0MemoryProvider()
        schema = {field["key"]: field for field in provider.get_config_schema()}

        assert "base_url" in schema
        assert schema["api_key"].get("required") is False

    def test_local_client_uses_x_api_key_header_only(self):
        headers = _LocalMem0Client("http://mem0.local", api_key="key")._headers()

        assert headers["X-API-Key"] == "key"
        assert "Authorization" not in headers

    def test_local_client_search_maps_filters_to_rest_body(self, monkeypatch):
        client = _LocalMem0Client("http://mem0.local", api_key="key")
        captured = {}

        def fake_request(method, path, *, json_body=None, query=None):
            captured.update({
                "method": method,
                "path": path,
                "json_body": json_body,
                "query": query,
            })
            return {"results": []}

        monkeypatch.setattr(client, "_request", fake_request)

        client.search(query="hello", filters={"user_id": "u123"}, top_k=3, threshold=0.7)

        assert captured["method"] == "POST"
        assert captured["path"] == "/search"
        assert captured["json_body"]["query"] == "hello"
        assert captured["json_body"]["filters"] == {"user_id": "u123"}
        assert captured["json_body"]["user_id"] == "u123"
        assert captured["json_body"]["top_k"] == 3
        assert captured["json_body"]["threshold"] == 0.7

    def test_local_client_add_maps_to_memories_endpoint(self, monkeypatch):
        client = _LocalMem0Client("http://mem0.local")
        captured = {}

        def fake_request(method, path, *, json_body=None, query=None):
            captured.update({
                "method": method,
                "path": path,
                "json_body": json_body,
                "query": query,
            })
            return {"results": []}

        monkeypatch.setattr(client, "_request", fake_request)

        client.add(
            [{"role": "user", "content": "user likes dark mode"}],
            user_id="u123",
            agent_id="hermes",
            infer=False,
        )

        assert captured["method"] == "POST"
        assert captured["path"] == "/memories"
        assert captured["json_body"]["user_id"] == "u123"
        assert captured["json_body"]["agent_id"] == "hermes"
        assert captured["json_body"]["infer"] is False

    def test_local_get_all_fetches_all_then_filters_locally(self, monkeypatch):
        client = _LocalMem0Client("http://mem0.local")
        captured = {}

        def fake_request(method, path, *, json_body=None, query=None):
            captured.update({
                "method": method,
                "path": path,
                "json_body": json_body,
                "query": query,
            })
            return {"results": [
                {"id": "1", "user_id": "u123", "memory": "keep"},
                {"id": "2", "user_id": "other", "memory": "drop"},
            ]}

        monkeypatch.setattr(client, "_request", fake_request)

        response = client.get_all(filters={"user_id": "u123"})

        assert captured["method"] == "GET"
        assert captured["path"] == "/memories"
        assert captured["query"] is None
        assert response == {"results": [{"id": "1", "user_id": "u123", "memory": "keep"}]}
