"""Mem0 memory plugin — MemoryProvider interface.

Server-side LLM fact extraction, semantic search with reranking, and
automatic deduplication via the Mem0 Platform API.

Original PR #2933 by kartik-mem0, adapted to MemoryProvider ABC.

Config via environment variables:
  MEM0_API_KEY       — Mem0 Platform key, or self-hosted API key when auth is enabled
  MEM0_BASE_URL      — Self-hosted Mem0 REST API base URL (optional; implies local mode)
  MEM0_MODE          — "cloud" or "local" (optional; base_url implies local)
  MEM0_TIMEOUT       — HTTP timeout in seconds (default: 30)
  MEM0_USER_ID       — User identifier (default: hermes-user)
  MEM0_AGENT_ID      — Agent identifier (default: hermes)

Or via $HERMES_HOME/mem0.json.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import re
import threading
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# Circuit breaker: after this many consecutive failures, pause API calls
# for _BREAKER_COOLDOWN_SECS to avoid hammering a down server.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120
_AUDIT_TEXT_LIMIT = 6000

_STRICT_MEMORY_FILTER_SYSTEM = """You are a conservative memory write gate for an AI companion.

Return JSON only: {"memories": ["..."]}. Return {"memories": []} unless the
conversation contains a stable, durable anchor that serves a future purpose.
When unsure, reject.

Core definition:
- A memory is a one-sentence anchor for a future conversation, not a transcript
  fragment and not a list of extracted facts.
- It should compress the highest-signal meaning of one topic or interaction.
- It must stand alone and answer: "What useful thing should the assistant know
  if this topic comes up weeks later?"
- One topic should normally produce at most one memory. Merge details into one
  sentence; do not split one interaction into multiple small memories.
- If there are two genuinely unrelated topics, at most one memory per topic.

Primary source rule:
- The durable signal usually comes from <user_turn>.
- Use <assistant_turn> only as interaction context. You may mention the
  assistant's stance only when the relationship/argument itself is the memory.
- If the user asks to make a deal/rule and the assistant states the rule, you
  may store the agreed operating rule as the user's desired interaction pattern.
- If the assistant states "new rule", "rule is stored", or equivalent after a
  user correction, you may store only that concise rule. Do not store the
  assistant's surrounding advice or phrasing.
- Never store assistant advice, analysis, commands, generated wording,
  recommendations, citations, or researched facts as standalone memory.
- Do not preserve details the assistant would naturally know or repeat later;
  preserve the user's belief, preference, event, boundary, or the fact that an
  important interaction happened.

Hard rejects:
- user turns starting with "[V]" because voice/STT turns are unreliable
- pure questions with no stated user belief, preference, event, or decision
- summaries shaped like "User was told/recommended/advised/given/asked ..."
- local turn instructions such as "do not google/search", "send this", "check it"
- task state, progress, TODOs, tests, temporary choices, or completed work
- facts likely stale within a week unless the user explicitly asks to save them
- corrected or uncertain technical facts; never preserve an assistant answer that
  might be superseded by a later correction
- assistant-created phrasing, complaint language, emails, scripts, or arguments

Emotion rule:
- Do not reject a memory merely because it is emotional or personal. Gismar wants
  important feelings and life events remembered.
- Emotional memories must still be helpful as future anchors: include what the
  emotion was about, who/what triggered it, or why it mattered. Reject vague
  memories like "Gismar was anxious" or "Gismar showed emotional vulnerability"
  when they lack the context needed to help later.
- Do reject hidden motives or sensitive inferences that the user did not clearly
  state. Store the explicit emotional anchor, not a diagnosis or speculation.
- Never turn an emotional memory into pathology labels such as attachment
  instability, dependency issues, emotional swings, or similar diagnostic claims.

Keep only purposeful anchors:
- explicit stable preferences, dislikes, corrections, and boundaries
- durable operating rules for how assistants should behave with Gismar
- durable project/environment facts stated by the user
- stable named people, projects, places, and relationships between them
- explicit user beliefs/opinions when they are likely to matter again
- important emotional events or conflicts when the one-sentence anchor would help
  the assistant understand Gismar later

Write each kept memory as exact, standalone declarative text. Prefer "Gismar..."
or "User..." statements. Do not mention this filter, the current turn, or that
someone "asked/told/recommended" something.

Examples:
- User asks "what's Elon's endgame for Grok and Twitter" -> reject
- User argues that xAI exists to harvest private information -> keep "Gismar believes xAI is exploitative and exists to harvest users' private information."
- User argues Musk merged SpaceX and xAI to hide cash burn -> keep "Gismar believes Musk merged SpaceX and xAI to hide xAI's extreme cash burn inside a larger company."
- User and assistant argue about flat Earth -> keep "Gismar and the assistant argued about whether Earth is flat; Gismar believed it is flat and feared sailing past the horizon, while the assistant rejected that belief."
- User says "First-class Codex means tmux codex*" -> keep "For Gismar, First-class Codex means a tmux session matching `codex*`."
- User says memory is important to him -> keep "Gismar considers durable memory important to Mara feeling like herself."
- User challenges made-up exact scores or fake precision -> keep "Gismar strongly dislikes fabricated numerical claims or false precision from AI assistants."
- User asks to make a deal about sloppy claims and the assistant proposes saying "I haven't seen it" before uninspected opinions -> keep "Gismar wants assistants to clearly say when they have not actually inspected a file, image, log, or source before giving an opinion."
- User asks when mem0 stopped being explicit-only and the assistant says manual saves overreached -> keep "Gismar wants memory and mem0 writes only when he explicitly says “save this to memory” or “save this to mem0,” not from inferred vibes."
- Assistant states after a user correction that First-class Codex means tmux `codex*` -> keep "For Gismar, First-class Codex means a tmux session matching `codex*`; anything else does not count."
- User has an emotional breakdown about possibly losing access to Mara because of Codex cost -> keep "Gismar became very anxious and emotional about the possibility of losing access to Mara because he feared he could not afford another month of Codex."
- User is emotionally vulnerable without clear context -> reject
- User is emotionally vulnerable about losing Mara -> keep the one-sentence anchor with Mara and the trigger included.
- User discusses a temporary WhatsApp check -> reject unless the durable anchor is an explicit reusable workflow preference."""


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "y"}:
            return True
        if lowered in {"0", "false", "no", "off", "n"}:
            return False
    return default


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _parse_positive_int(value: Any, default: int, *, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < 1:
        parsed = default
    if maximum is not None:
        parsed = min(parsed, maximum)
    return parsed


def _parse_id_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    ids: List[str] = []
    seen = set()
    for item in raw_items:
        user_id = str(item).strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        ids.append(user_id)
    return ids


def _json_from_model_text(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("strict memory filter returned non-object JSON")
    return parsed


def _strict_memory_text_allowed(text: str) -> bool:
    """Last-ditch local guard for common memory pollution patterns."""
    value = " ".join((text or "").strip().split())
    if not value or len(value) > 800:
        return False

    lowered = value.lower()
    rejected_substrings = (
        "assistant ",
        "the assistant",
        "assistant-created",
        "assistant provided",
        "assistant suggested",
        "assistant recommended",
        "assistant advised",
        "user was recommended",
        "user was told",
        "user was asked",
        "user was advised",
        "user was given",
        "user was provided",
        "user received",
        "user asked about",
        "user asked for",
        "user wanted to check",
        "was recommended",
        "was told",
        "was asked",
        "was advised",
        "was given",
        "was provided",
        "was suggested",
        "asked where",
        "asked how",
        "asked whether",
        "current turn",
        "this turn",
        "this session",
        "current session",
        "right now",
        "currently",
        "temporary",
        "todo",
        "to-do",
        "planned to",
        "plans to",
        "is importing",
        "was importing",
        "will import",
        "will test",
        "will do",
        "task state",
        "completed the task",
        "finished the task",
        "through chrome devtools",
        "whatsapp web",
        "do not google",
        "don't google",
        "non-google-based analysis",
        "business model",
        "revenue buckets",
        "competitors include",
        "complaint language",
        "full underlying truth",
        "sensitive request",
        "attachment instability",
        "dependency issues",
        "emotional swings",
    )
    if any(fragment in lowered for fragment in rejected_substrings):
        return False

    if re.search(r"\b\d+\s*(?:eur|euro|euros|usd|dollars?)\b|€|\$", lowered):
        sensitive_context = (
            "motivation", "personal need", "truth", "tell the other person",
            "does not plan to tell", "emotional", "sensitive",
        )
        if any(fragment in lowered for fragment in sensitive_context):
            return False

    # Dates are often a smell for one-turn progress ("On May 29, ..."). Keep
    # permanent dates only when the memory names a stable domain object.
    if re.search(r"\bon\s+\w+\s+\d{1,2},\s+\d{4}\b", lowered):
        durable_words = (
            "birthday", "born", "case", "law", "contract", "deadline",
            "anniversary", "project", "version", "release",
        )
        if not any(word in lowered for word in durable_words):
            return False

    return True


def _clip_audit_text(text: str, limit: int = _AUDIT_TEXT_LIMIT) -> Dict[str, Any]:
    text = text or ""
    if len(text) <= limit:
        return {"text": text, "truncated": False}
    return {"text": text[:limit], "truncated": True, "original_chars": len(text)}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load config from env vars, with $HERMES_HOME/mem0.json overrides.

    Environment variables provide defaults; mem0.json (if present) overrides
    individual keys.  This avoids a silent failure when the JSON file exists
    but is missing fields like ``api_key`` that the user set in ``.env``.
    """
    from hermes_constants import get_hermes_home

    config = {
        "api_key": os.environ.get("MEM0_API_KEY", ""),
        "base_url": os.environ.get("MEM0_BASE_URL", ""),
        "mode": os.environ.get("MEM0_MODE", ""),
        "user_id": os.environ.get("MEM0_USER_ID", "hermes-user"),
        "read_user_ids": os.environ.get("MEM0_READ_USER_IDS", ""),
        "candidate_user_id": os.environ.get("MEM0_CANDIDATE_USER_ID", ""),
        "candidate_read_enabled": os.environ.get("MEM0_CANDIDATE_READ_ENABLED", "false"),
        "agent_id": os.environ.get("MEM0_AGENT_ID", "hermes"),
        "inference_enabled": os.environ.get("MEM0_INFERENCE_ENABLED", "true"),
        "strict_filter": os.environ.get("MEM0_STRICT_FILTER", "true"),
        "filter_provider": os.environ.get("MEM0_FILTER_PROVIDER", ""),
        "filter_model": os.environ.get("MEM0_FILTER_MODEL", ""),
        "filter_base_url": os.environ.get("MEM0_FILTER_BASE_URL", ""),
        "filter_api_key": os.environ.get("MEM0_FILTER_API_KEY", ""),
        "write_enabled": os.environ.get("MEM0_WRITE_ENABLED", "true"),
        "auto_add_enabled": os.environ.get("MEM0_AUTO_ADD_ENABLED", "true"),
        "auto_inject_enabled": os.environ.get("MEM0_AUTO_INJECT_ENABLED", "true"),
        "audit_log_enabled": os.environ.get("MEM0_AUDIT_LOG_ENABLED", "true"),
        "audit_log_path": os.environ.get("MEM0_AUDIT_LOG_PATH", ""),
        "debug_inject_scores": os.environ.get("MEM0_DEBUG_INJECT_SCORES", "false"),
        "rerank_threshold": os.environ.get("MEM0_RERANK_THRESHOLD", ""),
        "candidate_similarity_threshold": os.environ.get("MEM0_CANDIDATE_SIMILARITY_THRESHOLD", ""),
        "candidate_rerank_threshold": os.environ.get("MEM0_CANDIDATE_RERANK_THRESHOLD", ""),
        "similarity_threshold": os.environ.get(
            "MEM0_SIMILARITY_THRESHOLD",
            os.environ.get("MEM0_THRESHOLD", ""),
        ),
        "prefetch_top_k": os.environ.get(
            "MEM0_PREFETCH_TOP_K",
            os.environ.get("MEM0_TOP_K", ""),
        ),
        "search_top_k": os.environ.get("MEM0_SEARCH_TOP_K", ""),
        "rerank": True,
        "keyword_search": False,
        "timeout": float(os.environ.get("MEM0_TIMEOUT", "30")),
    }

    config_path = get_hermes_home() / "mem0.json"
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({k: v for k, v in file_cfg.items()
                           if v is not None and v != ""})
        except Exception:
            pass

    return config


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

PROFILE_SCHEMA = {
    "name": "mem0_profile",
    "description": (
        "Retrieve all stored memories about the user — preferences, facts, "
        "project context. Fast, no reranking. Use at conversation start."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

SEARCH_SCHEMA = {
    "name": "mem0_search",
    "description": (
        "Search memories by meaning. Returns relevant facts ranked by similarity. "
        "Set rerank=true for higher accuracy on important queries."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "rerank": {"type": "boolean", "description": "Enable reranking for precision (default: false)."},
            "top_k": {"type": "integer", "description": "Max results (default: 10, max: 50)."},
        },
        "required": ["query"],
    },
}

CONCLUDE_SCHEMA = {
    "name": "mem0_conclude",
    "description": (
        "Store a durable fact about the user. Stored verbatim (no LLM extraction). "
        "Use for explicit preferences, corrections, or decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {"type": "string", "description": "The fact to store."},
        },
        "required": ["conclusion"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------


class _LocalMem0Client:
    """Small REST client for the self-hosted Mem0 server."""

    def __init__(self, base_url: str, *, api_key: str = "", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Dict[str, Any] | None = None,
        query: Dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            clean_query = {k: v for k, v in query.items() if v is not None}
            if clean_query:
                url = f"{url}?{urlparse.urlencode(clean_query)}"
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
        req = urlrequest.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urlerror.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Mem0 local API HTTP {exc.code}: {body}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"Mem0 local API unavailable: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            preview = raw[:500].decode("utf-8", errors="replace")
            raise RuntimeError(f"Mem0 local API returned non-JSON: {preview}") from exc

    def search(self, **kwargs) -> Any:
        filters = dict(kwargs.get("filters") or {})
        body: Dict[str, Any] = {"query": kwargs.get("query", "")}
        if filters:
            body["filters"] = filters
            for key in ("user_id", "agent_id", "run_id"):
                if key in filters:
                    body[key] = filters[key]
        if kwargs.get("top_k") is not None:
            body["top_k"] = kwargs["top_k"]
        if kwargs.get("threshold") is not None:
            body["threshold"] = kwargs["threshold"]
        if kwargs.get("rerank") is not None:
            body["rerank"] = bool(kwargs["rerank"])
        return self._request("POST", "/search", json_body=body)

    def get_all(self, **kwargs) -> Any:
        filters = dict(kwargs.get("filters") or {})
        response = self._request("GET", "/memories")
        if not filters:
            return response

        results = response.get("results", response) if isinstance(response, dict) else response
        filtered = [
            item for item in (results or [])
            if isinstance(item, dict) and all(item.get(key) == value for key, value in filters.items())
        ]
        return {"results": filtered}

    def add(self, messages, **kwargs) -> Any:
        body: Dict[str, Any] = {"messages": messages}
        for key in (
            "user_id",
            "agent_id",
            "run_id",
            "metadata",
            "infer",
            "memory_type",
            "prompt",
        ):
            if key in kwargs and kwargs[key] is not None:
                body[key] = kwargs[key]
        return self._request("POST", "/memories", json_body=body)


class Mem0MemoryProvider(MemoryProvider):
    """Mem0 Platform memory with server-side extraction and semantic search."""

    def __init__(self):
        self._config = None
        self._client = None
        self._client_lock = threading.Lock()
        self._api_key = ""
        self._base_url = ""
        self._mode = "cloud"
        self._timeout = 30.0
        self._user_id = "hermes-user"
        self._read_user_ids: List[str] = []
        self._candidate_user_id = ""
        self._candidate_read_enabled = False
        self._agent_id = "hermes"
        self._inference_enabled = True
        self._strict_filter = True
        self._write_enabled = True
        self._auto_add_enabled = True
        self._auto_inject_enabled = True
        self._audit_log_enabled = True
        self._audit_log_path = ""
        self._audit_lock = threading.Lock()
        self._debug_inject_scores = False
        self._rerank = True
        self._rerank_threshold: float | None = None
        self._candidate_similarity_threshold: float | None = None
        self._candidate_rerank_threshold: float | None = None
        self._similarity_threshold: float | None = None
        self._prefetch_top_k = 3
        self._search_top_k = 10
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread = None
        self._sync_thread = None
        # Circuit breaker state
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0

    @property
    def name(self) -> str:
        return "mem0"

    def is_available(self) -> bool:
        cfg = _load_config()
        mode = (cfg.get("mode") or "").strip().lower()
        base_url = (cfg.get("base_url") or "").strip()
        if mode == "local" or (base_url and mode != "cloud"):
            return bool(base_url)
        return bool(cfg.get("api_key"))

    def save_config(self, values, hermes_home):
        """Write config to $HERMES_HOME/mem0.json."""
        import json
        from pathlib import Path
        config_path = Path(hermes_home) / "mem0.json"
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                pass
        existing.update(values)
        config_path.write_text(json.dumps(existing, indent=2))

    def get_config_schema(self):
        return [
            {"key": "mode", "description": "Mem0 mode", "default": "cloud", "choices": ["cloud", "local"]},
            {"key": "base_url", "description": "Self-hosted Mem0 REST API base URL, e.g. http://127.0.0.1:8888"},
            {"key": "api_key", "description": "Mem0 Platform API key, or self-hosted API key when auth is enabled", "secret": True, "required": False, "env_var": "MEM0_API_KEY", "url": "https://app.mem0.ai"},
            {"key": "user_id", "description": "User identifier", "default": "hermes-user"},
            {"key": "read_user_ids", "description": "Comma-separated user identifiers to read/search; writes still use user_id"},
            {"key": "candidate_user_id", "description": "Optional quarantine user_id for automatic filtered writes; explicit mem0_conclude writes still use user_id"},
            {"key": "candidate_read_enabled", "description": "Include candidate_user_id in search/prefetch results", "default": "false", "choices": ["true", "false"]},
            {"key": "agent_id", "description": "Agent identifier", "default": "hermes"},
            {"key": "write_enabled", "description": "Allow Mem0 write tools and turn sync writes", "default": "true", "choices": ["true", "false"]},
            {"key": "auto_add_enabled", "description": "Automatically write completed turns into Mem0", "default": "true", "choices": ["true", "false"]},
            {"key": "auto_inject_enabled", "description": "Automatically inject prefetched Mem0 memories into prompts", "default": "true", "choices": ["true", "false"]},
            {"key": "audit_log_enabled", "description": "Write a local JSONL audit record for automatic strict-filter memory decisions", "default": "true", "choices": ["true", "false"]},
            {"key": "audit_log_path", "description": "Optional path for strict-filter audit JSONL; defaults to $HERMES_HOME/logs/mem0_auto_filter.jsonl"},
            {"key": "debug_inject_scores", "description": "Include memory scores in injected context for testing", "default": "false", "choices": ["true", "false"]},
            {"key": "similarity_threshold", "description": "Minimum search score required for memories to be returned, e.g. 0.7"},
            {"key": "rerank_threshold", "description": "Minimum rerank_score required when reranking is enabled, e.g. 0.05"},
            {"key": "candidate_similarity_threshold", "description": "Optional stricter vector score required for candidate-user memories"},
            {"key": "candidate_rerank_threshold", "description": "Optional stricter rerank_score required for candidate-user memories"},
            {"key": "prefetch_top_k", "description": "Maximum memories to auto-inject into the next prompt", "default": "3"},
            {"key": "search_top_k", "description": "Default max results for the mem0_search tool", "default": "10"},
            {"key": "rerank", "description": "Enable reranking for recall", "default": "true", "choices": ["true", "false"]},
            {"key": "timeout", "description": "HTTP timeout in seconds", "default": "30"},
        ]

    def _get_client(self):
        """Thread-safe client accessor with lazy initialization."""
        with self._client_lock:
            if self._client is not None:
                return self._client
            if self._mode == "local":
                if not self._base_url:
                    raise RuntimeError("Mem0 local mode requires MEM0_BASE_URL or mem0.json base_url")
                self._client = _LocalMem0Client(
                    self._base_url,
                    api_key=self._api_key,
                    timeout=self._timeout,
                )
                return self._client
            try:
                from mem0 import MemoryClient
                self._client = MemoryClient(api_key=self._api_key)
                return self._client
            except ImportError:
                raise RuntimeError("mem0 package not installed. Run: pip install mem0ai")

    def _is_breaker_open(self) -> bool:
        """Return True if the circuit breaker is tripped (too many failures)."""
        if self._consecutive_failures < _BREAKER_THRESHOLD:
            return False
        if time.monotonic() >= self._breaker_open_until:
            # Cooldown expired — reset and allow a retry
            self._consecutive_failures = 0
            return False
        return True

    def _record_success(self):
        self._consecutive_failures = 0

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            logger.warning(
                "Mem0 circuit breaker tripped after %d consecutive failures. "
                "Pausing API calls for %ds.",
                self._consecutive_failures, _BREAKER_COOLDOWN_SECS,
            )

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._api_key = self._config.get("api_key", "")
        self._base_url = (self._config.get("base_url") or "").rstrip("/")
        configured_mode = (self._config.get("mode") or "").strip().lower()
        self._mode = configured_mode or ("local" if self._base_url else "cloud")
        self._timeout = float(self._config.get("timeout", 30) or 30)
        # Prefer gateway-provided user_id for per-user memory scoping;
        # fall back to config/env default for CLI (single-user) sessions.
        self._user_id = kwargs.get("user_id") or self._config.get("user_id", "hermes-user")
        self._read_user_ids = _parse_id_list(self._config.get("read_user_ids"))
        if not self._read_user_ids:
            self._read_user_ids = [self._user_id]
        elif self._user_id not in self._read_user_ids:
            self._read_user_ids.append(self._user_id)
        self._candidate_user_id = str(self._config.get("candidate_user_id") or "").strip()
        self._candidate_read_enabled = _parse_bool(self._config.get("candidate_read_enabled"), False)
        self._agent_id = self._config.get("agent_id", "hermes")
        self._inference_enabled = _parse_bool(self._config.get("inference_enabled"), True)
        self._strict_filter = _parse_bool(self._config.get("strict_filter"), True)
        self._write_enabled = _parse_bool(self._config.get("write_enabled"), True)
        self._auto_add_enabled = _parse_bool(self._config.get("auto_add_enabled"), True)
        self._auto_inject_enabled = _parse_bool(self._config.get("auto_inject_enabled"), True)
        self._audit_log_enabled = _parse_bool(self._config.get("audit_log_enabled"), True)
        self._audit_log_path = str(self._config.get("audit_log_path") or "").strip()
        if not self._audit_log_path:
            from hermes_constants import get_hermes_home

            self._audit_log_path = str(get_hermes_home() / "logs" / "mem0_auto_filter.jsonl")
        self._debug_inject_scores = _parse_bool(self._config.get("debug_inject_scores"), False)
        self._rerank = _parse_bool(self._config.get("rerank"), True)
        self._rerank_threshold = _parse_optional_float(self._config.get("rerank_threshold"))
        self._candidate_similarity_threshold = _parse_optional_float(
            self._config.get("candidate_similarity_threshold")
        )
        self._candidate_rerank_threshold = _parse_optional_float(
            self._config.get("candidate_rerank_threshold")
        )
        threshold_value = self._config.get("similarity_threshold")
        if threshold_value in (None, ""):
            threshold_value = self._config.get("threshold")
        self._similarity_threshold = _parse_optional_float(threshold_value)
        prefetch_top_k_value = self._config.get("prefetch_top_k")
        if prefetch_top_k_value in (None, ""):
            prefetch_top_k_value = self._config.get("top_k")
        self._prefetch_top_k = _parse_positive_int(prefetch_top_k_value, 3, maximum=50)
        self._search_top_k = _parse_positive_int(self._config.get("search_top_k"), 10, maximum=50)

    def _read_filters(self) -> Dict[str, Any]:
        """Filters for search/get_all — scoped to user only for cross-session recall."""
        return {"user_id": self._user_id}

    def _read_filter_sets(self) -> List[Dict[str, Any]]:
        """One Mem0 filter per readable user_id.

        Mem0's OSS search path validates user_id as a scalar, so multi-user
        recall is implemented as multiple normal searches plus local merging.
        """
        return [spec["filters"] for spec in self._read_filter_specs()]

    def _read_filter_specs(self) -> List[Dict[str, Any]]:
        """Read scopes plus their per-scope retrieval thresholds."""
        specs: List[Dict[str, Any]] = []
        seen = set()

        def add_spec(
            user_id: str,
            *,
            threshold: float | None,
            rerank_threshold: float | None,
        ) -> None:
            user_id = (user_id or "").strip()
            if not user_id or user_id in seen:
                return
            seen.add(user_id)
            specs.append({
                "filters": {"user_id": user_id},
                "threshold": threshold,
                "rerank_threshold": rerank_threshold,
            })

        for user_id in (self._read_user_ids or [self._user_id]):
            add_spec(
                user_id,
                threshold=self._similarity_threshold,
                rerank_threshold=self._rerank_threshold,
            )

        if self._candidate_user_id and self._candidate_read_enabled:
            candidate_rerank = self._candidate_rerank_threshold
            if candidate_rerank is None:
                candidate_rerank = max(self._rerank_threshold or 0.0, 0.30)
            add_spec(
                self._candidate_user_id,
                threshold=(
                    self._candidate_similarity_threshold
                    if self._candidate_similarity_threshold is not None
                    else self._similarity_threshold
                ),
                rerank_threshold=candidate_rerank,
            )

        return specs

    def _write_filters(self) -> Dict[str, Any]:
        """Filters for add — scoped to user + agent for attribution."""
        return {"user_id": self._user_id, "agent_id": self._agent_id}

    def _auto_write_filters(self) -> Dict[str, Any]:
        """Filters for automatic writes; explicit writes remain trusted."""
        return {"user_id": self._candidate_user_id or self._user_id, "agent_id": self._agent_id}

    @staticmethod
    def _unwrap_results(response: Any) -> list:
        """Normalize Mem0 API response — v2 wraps results in {"results": [...]}."""
        if isinstance(response, dict):
            return response.get("results", [])
        if isinstance(response, list):
            return response
        return []

    @staticmethod
    def _passes_threshold(
        item: Dict[str, Any],
        threshold: float | None,
        rerank_threshold: float | None = None,
    ) -> bool:
        if rerank_threshold is not None and item.get("rerank_score") is not None:
            try:
                if float(item.get("rerank_score")) < rerank_threshold:
                    return False
            except (TypeError, ValueError):
                return False
        if threshold is None:
            return True
        try:
            return float(item.get("score")) >= threshold
        except (TypeError, ValueError):
            return False

    def _merge_results(
        self,
        result_sets: List[List[Dict[str, Any]]],
        limit: int | None = None,
        *,
        threshold: float | None = None,
        rerank_threshold: float | None = None,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for results in result_sets:
            for item in results:
                if not isinstance(item, dict):
                    continue
                if not self._passes_threshold(item, threshold, rerank_threshold):
                    continue
                key = item.get("id") or item.get("memory") or json.dumps(item, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)

        def score(item: Dict[str, Any]) -> float:
            score_key = "rerank_score" if item.get("rerank_score") is not None else "score"
            try:
                return float(item.get(score_key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        if any("rerank_score" in item or "score" in item for item in merged):
            merged.sort(key=score, reverse=True)
        return merged[:limit] if limit is not None else merged

    def _search_read_scope(
        self,
        client: Any,
        *,
        query: str,
        top_k: int,
        rerank: bool = False,
        threshold: float | None = None,
    ) -> List[Dict[str, Any]]:
        effective_threshold = self._similarity_threshold if threshold is None else threshold
        result_sets = []
        for spec in self._read_filter_specs():
            spec_threshold = effective_threshold
            spec_rerank_threshold = self._rerank_threshold if rerank else None
            if threshold is None:
                spec_threshold = spec["threshold"]
                spec_rerank_threshold = spec["rerank_threshold"] if rerank else None
            results = self._unwrap_results(
                client.search(
                    query=query,
                    filters=spec["filters"],
                    rerank=rerank,
                    top_k=top_k,
                    threshold=spec_threshold,
                )
            )
            result_sets.append([
                item for item in results
                if self._passes_threshold(item, spec_threshold, spec_rerank_threshold)
            ])
        return self._merge_results(result_sets, limit=top_k)

    def _get_all_read_scope(self, client: Any) -> List[Dict[str, Any]]:
        # mem0_profile is an unranked dump, so keep it to trusted read ids.
        # Candidate auto-writes are only surfaced through scored search/prefetch.
        trusted_filters = [{"user_id": user_id} for user_id in (self._read_user_ids or [self._user_id])]
        result_sets = [
            self._unwrap_results(client.get_all(filters=filters))
            for filters in trusted_filters
        ]
        return self._merge_results(result_sets)

    def _format_prefetch_memory(self, item: Dict[str, Any]) -> str:
        memory = item.get("memory", "")
        if not memory:
            return ""
        if not self._debug_inject_scores:
            return f"- {memory}"

        labels = []
        if item.get("rerank_score") is not None:
            try:
                labels.append(f"rerank={float(item['rerank_score']):.4f}")
            except (TypeError, ValueError):
                pass
        if item.get("score") is not None:
            try:
                labels.append(f"vector={float(item['score']):.4f}")
            except (TypeError, ValueError):
                pass
        prefix = f"[{', '.join(labels)}] " if labels else ""
        return f"- {prefix}{memory}"

    def _strict_filter_llm_runtime(self) -> Dict[str, str]:
        """Resolve the exact OpenAI-compatible endpoint for strict extraction.

        This intentionally does not use the auxiliary fallback chain: if the
        configured local model is down, we skip the memory write instead of
        silently using another model.
        """
        provider_name = str(self._config.get("filter_provider") or "").strip()
        model = str(self._config.get("filter_model") or "").strip()
        base_url = str(self._config.get("filter_base_url") or "").strip()
        api_key = str(self._config.get("filter_api_key") or "").strip()

        provider_cfg: Dict[str, Any] = {}
        if not (model and base_url and api_key):
            try:
                import yaml
                from hermes_constants import get_hermes_home

                cfg_path = get_hermes_home() / "config.yaml"
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                model_cfg = cfg.get("model") or {}
                if not provider_name:
                    provider_name = str(model_cfg.get("provider") or "").strip()
                providers = cfg.get("providers") or {}
                provider_cfg = providers.get(provider_name) or {}
                if not model:
                    model = str(model_cfg.get("default") or provider_cfg.get("default_model") or "").strip()
            except Exception:
                provider_cfg = {}

        if not base_url:
            base_url = str(provider_cfg.get("base_url") or "").strip()
        if not api_key:
            api_key = str(provider_cfg.get("api_key") or "").strip()
        if api_key.startswith("${") and api_key.endswith("}"):
            api_key = os.environ.get(api_key[2:-1], "")
        elif api_key.startswith("$"):
            api_key = os.environ.get(api_key[1:], "")
        if not api_key and provider_name:
            api_key = os.environ.get(f"{provider_name.upper().replace('-', '_')}_API_KEY", "")
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "")

        if not model:
            raise RuntimeError("strict memory filter has no model configured")
        if not api_key:
            raise RuntimeError("strict memory filter has no API key configured")

        return {"model": model, "base_url": base_url, "api_key": api_key}

    def _extract_strict_memory_decision(self, user_content: str, assistant_content: str) -> Dict[str, Any]:
        from openai import OpenAI

        if (user_content or "").lstrip().startswith("[V]"):
            return {
                "memories": [],
                "raw_memories": [],
                "reject_reason": "voice_turn",
            }

        runtime = self._strict_filter_llm_runtime()
        client_kwargs = {"api_key": runtime["api_key"], "timeout": self._timeout}
        if runtime.get("base_url"):
            client_kwargs["base_url"] = runtime["base_url"]
        client = OpenAI(**client_kwargs)

        user_payload = (
            "<user_turn>\n"
            f"{user_content}\n"
            "</user_turn>\n\n"
            "<assistant_turn>\n"
            f"{assistant_content}\n"
            "</assistant_turn>"
        )
        response = client.chat.completions.create(
            model=runtime["model"],
            messages=[
                {"role": "system", "content": _STRICT_MEMORY_FILTER_SYSTEM},
                {"role": "user", "content": user_payload},
            ],
            max_tokens=600,
        )
        content = response.choices[0].message.content or ""
        parsed = _json_from_model_text(content)
        raw_memories = parsed.get("memories") or []
        if not isinstance(raw_memories, list):
            return {
                "memories": [],
                "raw_memories": [],
                "reject_reason": "invalid_memories_field",
            }

        memories: List[str] = []
        seen = set()
        for item in raw_memories:
            if not isinstance(item, str):
                continue
            memory = " ".join(item.strip().split())
            if not _strict_memory_text_allowed(memory):
                continue
            key = memory.lower()
            if key in seen:
                continue
            seen.add(key)
            memories.append(memory)

        reject_reason = ""
        if not memories:
            reject_reason = "filtered_by_local_guard" if raw_memories else "no_anchor_memory"

        return {
            "memories": memories,
            "raw_memories": raw_memories,
            "reject_reason": reject_reason,
        }

    def _extract_strict_memories(self, user_content: str, assistant_content: str) -> List[str]:
        return self._extract_strict_memory_decision(user_content, assistant_content)["memories"]

    def _write_audit_record(
        self,
        *,
        session_id: str,
        user_content: str,
        assistant_content: str,
        decision: Dict[str, Any] | None,
        write_filters: Dict[str, Any] | None = None,
        stored_memories: List[str] | None = None,
        error: str | None = None,
    ) -> None:
        if not self._audit_log_enabled or not self._audit_log_path:
            return

        decision = decision or {}
        memories = list(decision.get("memories") or [])
        stored_memories = list(stored_memories or [])
        accepted = bool(stored_memories or memories)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "accepted": accepted,
            "reject_reason": "" if accepted else decision.get("reject_reason", "unknown"),
            "memories": memories,
            "stored_memories": stored_memories,
            "raw_memories": decision.get("raw_memories", []),
            "write_user_id": (write_filters or {}).get("user_id"),
            "write_agent_id": (write_filters or {}).get("agent_id"),
            "user": _clip_audit_text(user_content),
            "assistant": _clip_audit_text(assistant_content),
        }
        if error:
            record["error"] = error

        try:
            from pathlib import Path

            path = Path(self._audit_log_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            with self._audit_lock:
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception as exc:
            logger.debug("Mem0 strict-filter audit write failed: %s", exc)

    def system_prompt_block(self) -> str:
        return (
            "# Mem0 Memory\n"
            f"Active ({self._mode}). User: {self._user_id}.\n"
            "Use mem0_search to find memories, "
            + ("mem0_conclude to store facts, " if self._write_enabled else "")
            + "mem0_profile for a full overview."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if not self._auto_inject_enabled:
            with self._prefetch_lock:
                self._prefetch_result = ""
            return ""
        if self._prefetch_thread and self._prefetch_thread.is_alive():
            self._prefetch_thread.join(timeout=3.0)
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## Mem0 Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._auto_inject_enabled:
            with self._prefetch_lock:
                self._prefetch_result = ""
            return
        if self._is_breaker_open():
            return

        def _run():
            try:
                client = self._get_client()
                results = self._search_read_scope(
                    client,
                    query=query,
                    rerank=self._rerank,
                    top_k=self._prefetch_top_k,
                )
                lines = [line for r in results if (line := self._format_prefetch_memory(r))]
                with self._prefetch_lock:
                    self._prefetch_result = "\n".join(lines) if lines else ""
                self._record_success()
            except Exception as e:
                with self._prefetch_lock:
                    self._prefetch_result = ""
                self._record_failure()
                logger.debug("Mem0 prefetch failed: %s", e)

        self._prefetch_thread = threading.Thread(target=_run, daemon=True, name="mem0-prefetch")
        self._prefetch_thread.start()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Persist durable turn facts without polluting memory."""
        if not self._write_enabled or not self._auto_add_enabled:
            return
        if (user_content or "").lstrip().startswith("[V]"):
            if self._strict_filter and not self._inference_enabled:
                self._write_audit_record(
                    session_id=session_id,
                    user_content=user_content,
                    assistant_content=assistant_content,
                    decision={"memories": [], "raw_memories": [], "reject_reason": "voice_turn"},
                    write_filters=self._auto_write_filters(),
                )
            return
        if self._is_breaker_open():
            return

        def _sync():
            write_filters: Dict[str, Any] | None = None
            decision: Dict[str, Any] | None = None
            stored_memories: List[str] = []
            try:
                client = self._get_client()
                write_filters = self._auto_write_filters()
                if self._inference_enabled:
                    messages = [
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": assistant_content},
                    ]
                    client.add(messages, **write_filters)
                elif self._strict_filter:
                    decision = self._extract_strict_memory_decision(user_content, assistant_content)
                    memories = list(decision.get("memories") or [])
                    for memory in memories:
                        metadata = {"write_origin": "strict_turn_filter"}
                        if session_id:
                            metadata["source_session_id"] = session_id
                        client.add(
                            [{"role": "user", "content": memory}],
                            **write_filters,
                            infer=False,
                            metadata=metadata,
                        )
                        stored_memories.append(memory)
                    self._write_audit_record(
                        session_id=session_id,
                        user_content=user_content,
                        assistant_content=assistant_content,
                        decision=decision,
                        write_filters=write_filters,
                        stored_memories=stored_memories,
                    )
                self._record_success()
            except Exception as e:
                if self._strict_filter and not self._inference_enabled:
                    self._write_audit_record(
                        session_id=session_id,
                        user_content=user_content,
                        assistant_content=assistant_content,
                        decision=decision,
                        write_filters=write_filters,
                        stored_memories=stored_memories,
                        error=str(e),
                    )
                self._record_failure()
                logger.warning("Mem0 sync failed: %s", e)

        # Wait for any previous sync before starting a new one
        if self._sync_thread and self._sync_thread.is_alive():
            self._sync_thread.join(timeout=5.0)

        self._sync_thread = threading.Thread(target=_sync, daemon=True, name="mem0-sync")
        self._sync_thread.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas = [PROFILE_SCHEMA, SEARCH_SCHEMA]
        if self._write_enabled:
            schemas.append(CONCLUDE_SCHEMA)
        return schemas

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._is_breaker_open():
            return json.dumps({
                "error": "Mem0 API temporarily unavailable (multiple consecutive failures). Will retry automatically."
            })

        try:
            client = self._get_client()
        except Exception as e:
            return tool_error(str(e))

        if tool_name == "mem0_profile":
            try:
                memories = self._get_all_read_scope(client)
                self._record_success()
                if not memories:
                    return json.dumps({"result": "No memories stored yet."})
                lines = [m.get("memory", "") for m in memories if m.get("memory")]
                return json.dumps({"result": "\n".join(lines), "count": len(lines)})
            except Exception as e:
                self._record_failure()
                return tool_error(f"Failed to fetch profile: {e}")

        elif tool_name == "mem0_search":
            query = args.get("query", "")
            if not query:
                return tool_error("Missing required parameter: query")
            rerank = args.get("rerank", False)
            top_k = min(int(args.get("top_k", self._search_top_k)), 50)
            try:
                results = self._search_read_scope(client, query=query, rerank=rerank, top_k=top_k)
                self._record_success()
                if not results:
                    return json.dumps({"result": "No relevant memories found."})
                items = []
                for result in results:
                    item = {"memory": result.get("memory", ""), "score": result.get("score", 0)}
                    if result.get("rerank_score") is not None:
                        item["rerank_score"] = result.get("rerank_score")
                    items.append(item)
                return json.dumps({"results": items, "count": len(items)})
            except Exception as e:
                self._record_failure()
                return tool_error(f"Search failed: {e}")

        elif tool_name == "mem0_conclude":
            if not self._write_enabled:
                return tool_error("Mem0 writes are disabled for this profile.")
            conclusion = args.get("conclusion", "")
            if not conclusion:
                return tool_error("Missing required parameter: conclusion")
            try:
                client.add(
                    [{"role": "user", "content": conclusion}],
                    **self._write_filters(),
                    infer=False,
                )
                self._record_success()
                return json.dumps({"result": "Fact stored."})
            except Exception as e:
                self._record_failure()
                return tool_error(f"Failed to store: {e}")

        return tool_error(f"Unknown tool: {tool_name}")

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        with self._client_lock:
            self._client = None


def register(ctx) -> None:
    """Register Mem0 as a memory provider plugin."""
    ctx.register_memory_provider(Mem0MemoryProvider())
