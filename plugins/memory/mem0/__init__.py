"""Mem0 memory plugin — MemoryProvider interface.

Server-side LLM fact extraction, semantic search, and automatic deduplication
via the Mem0 Platform API (cloud) or OSS (self-hosted) via Memory.

Original PR #2933 by kartik-mem0, adapted to MemoryProvider ABC.

Configuration
-------------
Secret (lives in $HERMES_HOME/.env or the environment):
  MEM0_API_KEY       — Mem0 Platform API key (required for platform mode)
  MEM0_HOST          — Base URL of a self-hosted Mem0 server. When set, the
                       plugin talks to that server directly over HTTP
                       (X-API-Key auth) instead of the cloud API.

Behavioral settings (live in $HERMES_HOME/mem0.json, set via `hermes memory
setup`):
  mode               — Backend mode: "platform" (default), "oss", or "local"
  host               — Self-hosted Mem0 server URL (alt: MEM0_HOST env var).
                       When set, routes to the self-hosted HTTP backend.
  base_url           — Base URL for the explicit "local" REST mode.
  user_id            — Canonical user identifier. When set, it is applied
                       uniformly across every gateway (CLI, Telegram, Slack,
                       Discord, …) so the same human gets one merged memory
                       store. When unset, the gateway-native id (e.g. Telegram
                       numeric id, Discord snowflake) is used instead.
  agent_id           — Agent identifier (default: hermes)

The matching MEM0_MODE / MEM0_USER_ID / MEM0_AGENT_ID environment variables are
still read as a backward-compatible fallback, but mem0.json is the canonical
home for these non-secret settings.
"""

from __future__ import annotations

import atexit
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, List

from agent.memory_provider import MemoryProvider
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# Circuit breaker: after this many consecutive failures, pause API calls
# for _BREAKER_COOLDOWN_SECS to avoid hammering a down server.
_BREAKER_THRESHOLD = 5
_BREAKER_COOLDOWN_SECS = 120
_PREFETCH_WAIT_SECS = 3
_MAX_PREFETCH_TOP_K = 3
_DEFAULT_SEARCH_TOP_K = 10
_DEFAULT_INJECTION_THRESHOLD = 0.7
_RECENCY_BLOCK_SECONDS = 10 * 60
_SAME_SESSION_BLOCK_SECONDS = 60 * 60

_CLIENT_ERROR_TYPES = ("MemoryNotFoundError", "ValidationError")

# Sentinel returned when neither MEM0_USER_ID nor a gateway-native id is
# available. Treated as "no operator-configured user_id" by initialize() so
# that legacy mem0.json files written by the setup wizard (which historically
# wrote this exact placeholder) still allow gateway-native ids to flow
# through instead of silently overriding them with the placeholder.
_DEFAULT_USER_ID = "hermes-user"


def _is_client_error(exc: Exception) -> bool:
    """True for user-caused errors (bad ID, not found) that should NOT trip circuit breaker."""
    etype = type(exc).__name__
    if etype in _CLIENT_ERROR_TYPES:
        return True
    err_str = str(exc).lower()
    return "404" in err_str or "not found" in err_str or "valid uuid" in err_str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _parse_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _memory_metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _memory_source(item: dict[str, Any]) -> str:
    metadata = _memory_metadata(item)
    return str(item.get("source") or metadata.get("source") or "").strip().lower()


def _memory_created_at(item: dict[str, Any]) -> datetime | None:
    metadata = _memory_metadata(item)
    return _parse_time(item.get("created_at") or metadata.get("created_at_iso") or metadata.get("created_at"))


def _memory_session_id(item: dict[str, Any]) -> str:
    metadata = _memory_metadata(item)
    return str(
        item.get("session_id")
        or item.get("source_session_id")
        or metadata.get("session_id")
        or metadata.get("source_session_id")
        or ""
    )


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
        "mode": os.environ.get("MEM0_MODE", "platform"),
        "api_key": os.environ.get("MEM0_API_KEY", ""),
        "host": os.environ.get("MEM0_HOST", ""),
        "agent_id": os.environ.get("MEM0_AGENT_ID", "hermes"),
        "oss": {},
    }
    # Only carry user_id when the operator explicitly configured one (env or
    # mem0.json). An absent key tells initialize() to fall back to the
    # gateway-native id from kwargs instead of overriding it with a placeholder.
    env_user_id = os.environ.get("MEM0_USER_ID")
    if env_user_id:
        config["user_id"] = env_user_id

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

SEARCH_SCHEMA = {
    "name": "mem0_search",
    "description": (
        "Search the user's memories by meaning; returns facts ranked by "
        "relevance. Use this before answering any question that may depend on "
        "what you know about the user (preferences, facts, history, people, "
        "projects, past decisions). For multi-part or multi-hop questions, "
        "call it several times — vary the wording and run follow-up searches "
        "on what earlier results reveal; one search is rarely enough."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "top_k": {"type": "integer", "description": "Max results (default: 10, max: 50)."},
            "rerank": {"type": "boolean", "description": "Rerank results for relevance (default: false, platform mode only)."},
        },
        "required": ["query"],
    },
}

ADD_SCHEMA = {
    "name": "mem0_add",
    "description": (
        "Store a durable fact about the user, verbatim (no LLM extraction). "
        "Call this the moment the user states a lasting preference, correction, "
        "decision, or personal detail worth recalling on future turns — don't "
        "wait to be asked to remember. Skip transient chit-chat and facts you've "
        "already stored."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact to store."},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional short tags for the stored fact.",
            },
        },
        "required": ["content"],
    },
}

UPDATE_SCHEMA = {
    "name": "mem0_update",
    "description": (
        "Replace the text of an existing memory by its ID (take the ID from a "
        "mem0_search result). Use when a stored fact has changed "
        "or was wrong — correct it in place instead of adding a duplicate."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Memory UUID to update."},
            "text": {"type": "string", "description": "New text content."},
        },
        "required": ["memory_id", "text"],
    },
}

DELETE_SCHEMA = {
    "name": "mem0_delete",
    "description": (
        "Delete a memory by its ID (take the ID from a mem0_search "
        "result). Use when a stored fact is obsolete or the user asks you to "
        "forget it; prefer mem0_update if the fact merely changed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Memory UUID to delete."},
        },
        "required": ["memory_id"],
    },
}


# ---------------------------------------------------------------------------
# MemoryProvider implementation
# ---------------------------------------------------------------------------

class Mem0MemoryProvider(MemoryProvider):
    """Mem0 memory with server-side extraction and semantic search.

    Supports Platform API, self-hosted HTTP, local REST, and OSS modes.
    """

    def __init__(self):
        self._config = None
        self._backend = None
        self._mode = "platform"
        self._api_key = ""
        self._host = ""
        self._user_id = _DEFAULT_USER_ID
        self._agent_id = "hermes"
        self._rerank_default = False
        self._session_id = ""
        self._hermes_home = None
        self._channel = "cli"  # gateway channel name (cli/telegram/discord/...)
        self._turn_number = 0
        self._sync_thread = None
        self._prefetch_thread = None
        self._prefetch_query = ""
        self._prefetch_result = ""
        self._prefetch_done = False
        # Circuit breaker state
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0
        self._breaker_lock = threading.Lock()
        self._sync_lock = threading.Lock()
        self._prefetch_lock = threading.Lock()
        self._atexit_registered = False

    @property
    def name(self) -> str:
        return "mem0"

    def is_available(self) -> bool:
        cfg = _load_config()
        mode = cfg.get("mode", "platform")
        if mode == "local":
            return bool(cfg.get("base_url"))
        if mode == "oss":
            return bool(cfg.get("oss", {}).get("vector_store"))
        # Platform needs an api_key; self-hosted needs a host (api_key optional
        # when the server runs with AUTH_DISABLED).
        return bool(cfg.get("api_key") or cfg.get("host"))

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
        from utils import atomic_json_write
        atomic_json_write(config_path, existing, mode=0o600)

    def get_config_schema(self):
        cfg = _load_config()
        mode = cfg.get("mode", "platform")
        api_key_required = mode != "oss"
        return [
            {"key": "api_key", "description": "Mem0 Platform API key", "secret": True, "required": api_key_required, "env_var": "MEM0_API_KEY", "url": "https://app.mem0.ai"},
            {"key": "host", "description": "Self-hosted Mem0 server URL (leave blank for cloud)", "required": False, "env_var": "MEM0_HOST"},
            {"key": "base_url", "description": "Local Mem0 REST API URL (local mode only)", "required": False},
            {"key": "user_id", "description": "User identifier", "default": "hermes-user"},
            {"key": "agent_id", "description": "Agent identifier", "default": "hermes"},
            {"key": "rerank", "description": "Enable reranking for recall", "default": "false", "choices": ["true", "false"]},
        ]

    def post_setup(self, hermes_home: str, config: dict) -> None:
        from ._setup import post_setup
        post_setup(hermes_home, config)

    def _create_backend(self):
        # Lazy-install the mem0 SDK on demand before either backend imports
        # it. ensure() honors security.allow_lazy_installs (default true) and,
        # on a sealed Docker venv, redirects the install to the durable
        # target. On failure we fall through so the import inside the backend
        # produces the canonical error, captured below.
        try:
            from tools.lazy_deps import ensure as _lazy_ensure
            _lazy_ensure("memory.mem0", prompt=False)
        except ImportError:
            pass
        except Exception:
            pass
        try:
            if self._mode == "local":
                from ._backend import LocalRESTBackend
                timeout = float(self._config.get("timeout", 60) or 60)
                return LocalRESTBackend(
                    self._config.get("base_url", ""),
                    api_key=self._api_key,
                    timeout=timeout,
                )
            if self._mode == "oss":
                from ._backend import OSSBackend
                return OSSBackend(self._config.get("oss", {}))
            if self._host:
                from ._backend import SelfHostedBackend
                return SelfHostedBackend(self._api_key, self._host)
            from ._backend import PlatformBackend
            return PlatformBackend(self._api_key)
        except Exception as e:
            logger.error("Mem0 backend failed to initialize (%s mode): %s", self._mode, e)
            self._init_error = str(e)
            return None

    def _is_breaker_open(self) -> bool:
        """Return True if the circuit breaker is tripped (too many failures)."""
        with self._breaker_lock:
            if self._consecutive_failures < _BREAKER_THRESHOLD:
                return False
            if time.monotonic() >= self._breaker_open_until:
                self._consecutive_failures = 0
                return False
            return True

    def _format_error(self, prefix: str, exc: Exception) -> str:
        msg = f"{prefix}: {exc}"
        if self._mode in {"local", "oss"} or self._host:
            err_str = str(exc).lower()
            if "connection" in err_str or "refused" in err_str or "timeout" in err_str:
                if self._mode == "local":
                    msg += f" (check that {self._config.get('base_url', 'local Mem0')} is running)"
                elif self._host:
                    msg += f" (check that {self._host} is running and reachable)"
                else:
                    vs = self._config.get("oss", {}).get("vector_store", {})
                    msg += f" (check that {vs.get('provider', 'vector store')} is running)"
        return msg

    def _record_success(self):
        with self._breaker_lock:
            self._consecutive_failures = 0

    def _record_failure(self):
        with self._breaker_lock:
            self._consecutive_failures += 1
            count = self._consecutive_failures
            if count >= _BREAKER_THRESHOLD:
                self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN_SECS
            else:
                count = 0
        if count >= _BREAKER_THRESHOLD:
            hint = ""
            if self._mode == "oss":
                vs = self._config.get("oss", {}).get("vector_store", {})
                provider = vs.get("provider", "unknown")
                hint = f" Check that your {provider} vector store is running and reachable."
            elif self._host:
                hint = f" Check that {self._host} is running and reachable."
            logger.warning(
                "Mem0 circuit breaker tripped after %d consecutive failures. "
                "Pausing API calls for %ds.%s",
                count, _BREAKER_COOLDOWN_SECS, hint,
            )

    def initialize(self, session_id: str, **kwargs) -> None:
        self._config = _load_config()
        self._mode = self._config.get("mode", "platform")
        self._api_key = self._config.get("api_key", "")
        self._host = self._config.get("host", "")
        self._session_id = session_id
        self._hermes_home = Path(kwargs.get("hermes_home") or os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
        # Resolution order for user_id:
        #   1. Operator-configured MEM0_USER_ID (env or $HERMES_HOME/mem0.json) —
        #      the canonical principal, applied across every gateway so the same
        #      human gets one merged memory store.
        #   2. Gateway-native id from kwargs (Telegram numeric id, Discord
        #      snowflake, etc.) — preserves per-platform isolation when no
        #      override is configured.
        #   3. Hardcoded fallback _DEFAULT_USER_ID (CLI with no auth).
        # The literal _DEFAULT_USER_ID string is treated as unset so users who
        # ran the setup wizard with the suggested default still get gateway-
        # native ids instead of being silently bucketed together.
        configured = self._config.get("user_id")
        if configured == _DEFAULT_USER_ID:
            configured = None
        self._user_id = configured or kwargs.get("user_id") or _DEFAULT_USER_ID
        self._agent_id = self._config.get("agent_id", "hermes")
        # Persisted rerank preference (setup wizard / mem0.json). Used as the
        # DEFAULT for mem0_search when the model doesn't pass ``rerank``
        # explicitly; per-call args still win. Platform-only feature — other
        # backends accept-and-ignore the flag.
        _rr = self._config.get("rerank", False)
        self._rerank_default = (
            _rr.lower() in ("true", "1", "yes") if isinstance(_rr, str) else bool(_rr)
        )
        self._channel = kwargs.get("platform") or "cli"
        self._backend = self._create_backend()
        if self._backend and not self._atexit_registered:
            atexit.register(self._shutdown_backend)
            self._atexit_registered = True

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        self._session_id = new_session_id

    def _config_bool(self, key: str, default: bool = False) -> bool:
        cfg = self._config if isinstance(self._config, dict) else {}
        return _parse_bool(cfg.get(key), default)

    def _write_enabled(self) -> bool:
        return self._config_bool("write_enabled", True)

    def _auto_add_enabled(self) -> bool:
        return self._config_bool("auto_add_enabled", False)

    def _inference_enabled(self) -> bool:
        return self._config_bool("inference_enabled", False)

    def _auto_inject_enabled(self) -> bool:
        return self._config_bool("auto_inject_enabled", True)

    def _read_filters(self) -> Dict[str, Any]:
        # Scoped to user_id only — by design — so recall surfaces memories
        # written from any gateway/agent under this principal. Writes attach
        # agent_id (and metadata.channel) so per-agent / per-channel views are
        # still possible at query time when needed; reads default to the wider
        # cross-agent recall.
        return {"user_id": self._user_id}

    def _read_user_ids(self) -> list[str]:
        cfg = self._config if isinstance(self._config, dict) else {}
        configured = cfg.get("read_user_ids")
        if isinstance(configured, str):
            items = [part.strip() for part in configured.replace(";", ",").split(",")]
        elif isinstance(configured, list):
            items = [str(part).strip() for part in configured]
        else:
            items = []
        if self._config_bool("candidate_read_enabled", False):
            candidate_user_id = str(cfg.get("candidate_user_id") or "").strip()
            if candidate_user_id:
                items.append(candidate_user_id)
        items = [item for item in items if item]
        if not items:
            items = [self._user_id]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def _write_metadata(self, extra: dict[str, Any] | None = None) -> Dict[str, Any]:
        # Tag every write with the gateway channel so the dashboard can offer
        # per-channel filtered views without coupling identity to the channel.
        metadata = {"channel": self._channel} if self._channel else {}
        if extra:
            metadata.update({key: value for key, value in extra.items() if value is not None})
        return metadata

    def _prefetch_top_k(self) -> int:
        cfg = self._config if isinstance(self._config, dict) else {}
        try:
            configured = int(cfg.get("prefetch_top_k", _MAX_PREFETCH_TOP_K))
        except (TypeError, ValueError):
            configured = _MAX_PREFETCH_TOP_K
        return max(1, min(configured, _MAX_PREFETCH_TOP_K))

    def _search_top_k(self) -> int:
        cfg = self._config if isinstance(self._config, dict) else {}
        return max(1, min(_parse_int(cfg.get("search_top_k"), _DEFAULT_SEARCH_TOP_K), 50))

    def _injection_threshold(self) -> float:
        cfg = self._config if isinstance(self._config, dict) else {}
        value = cfg.get("rerank_threshold", cfg.get("similarity_threshold"))
        return _parse_float(value, _DEFAULT_INJECTION_THRESHOLD)

    def _audit_path(self) -> Path:
        cfg = self._config if isinstance(self._config, dict) else {}
        configured = str(cfg.get("audit_log_path") or "").strip()
        if configured:
            return Path(configured).expanduser()
        home = self._hermes_home or Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
        return Path(home) / "logs" / "mem0_live_injection_audit.jsonl"

    def _search_all_read_users(self, backend, query: str, *, top_k: int, rerank: bool) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for user_id in self._read_user_ids():
            for item in backend.search(query=query, filters={"user_id": user_id}, top_k=top_k, rerank=rerank):
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                key = item_id or f"{user_id}:{item.get('memory', '')}"
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                merged.append(item)
        merged.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
        return merged[:top_k]

    def _candidate_audit_item(
        self,
        item: dict[str, Any],
        *,
        now: datetime,
        reason: str = "",
    ) -> dict[str, Any]:
        created_at = _memory_created_at(item)
        age_seconds = None
        if created_at:
            age_seconds = max(0, int((now - created_at).total_seconds()))
        return {
            "id": item.get("id"),
            "text": item.get("memory", ""),
            "score": item.get("score"),
            "created_at": item.get("created_at") or _memory_metadata(item).get("created_at_iso"),
            "source": _memory_source(item),
            "age_seconds": age_seconds,
            "reason": reason,
        }

    def _select_injected_memories(
        self,
        results: list[dict[str, Any]],
        *,
        session_id: str,
        top_k: int,
        now: datetime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        injected: list[dict[str, Any]] = []
        audited: list[dict[str, Any]] = []
        first_block_reason = "empty"
        threshold = self._injection_threshold()
        active_session_id = session_id or self._session_id
        for item in results:
            reason = "inject"
            score = item.get("score")
            if score is not None:
                try:
                    if float(score) < threshold:
                        reason = "below_threshold"
                except (TypeError, ValueError):
                    pass
            source = _memory_source(item)
            if reason == "inject" and source != "explicit":
                reason = "inferred_block"
            created_at = _memory_created_at(item)
            age_seconds = None
            if created_at:
                age_seconds = max(0, int((now - created_at).total_seconds()))
            if reason == "inject" and age_seconds is not None and age_seconds < _RECENCY_BLOCK_SECONDS:
                reason = "recency_block"
            if (
                reason == "inject"
                and active_session_id
                and _memory_session_id(item) == active_session_id
                and age_seconds is not None
                and age_seconds < _SAME_SESSION_BLOCK_SECONDS
            ):
                reason = "same_session_block"
            audited.append(self._candidate_audit_item(item, now=now, reason=reason))
            if reason == "inject" and len(injected) < top_k:
                injected.append(item)
            elif reason != "inject" and first_block_reason == "empty":
                first_block_reason = reason
        reason = "inject" if injected else first_block_reason
        return injected, audited, reason

    def _write_injection_audit(
        self,
        *,
        inject: bool,
        reason: str,
        mode: str,
        session_id: str,
        query: str,
        candidates: list[dict[str, Any]],
        injected: list[dict[str, Any]],
    ) -> None:
        if not self._config_bool("audit_log_enabled", True):
            return
        record = {
            "timestamp": _utc_now_iso(),
            "inject": inject,
            "reason": reason,
            "turn": self._turn_number,
            "mode": mode,
            "session": session_id or self._session_id,
            "query": query,
            "candidates": candidates,
            "injected": [item.get("id") for item in injected],
        }
        try:
            path = self._audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("Failed to write Mem0 injection audit", exc_info=True)

    def system_prompt_block(self) -> str:
        # Mirror the precedence in _create_backend (local > oss > host >
        # platform) so
        # the label always names the backend that actually runs. Checking
        # ``host`` first here would mislabel an ``oss``+``host`` config as
        # self-hosted HTTP even though OSS wins the routing.
        if self._mode == "local":
            mode_label = f"local REST API ({self._config.get('base_url', 'unconfigured')})"
        elif self._mode == "oss":
            mode_label = "OSS (self-hosted)"
        elif self._host:
            mode_label = "self-hosted (HTTP API)"
        else:
            mode_label = "platform (cloud API)"
        # Rerank is a Mem0 Platform feature only.
        rerank_note = " Rerank is available on search." if (self._mode == "platform" and not self._host) else ""
        return (
            "# Mem0 Memory\n"
            f"Active. Mode: {mode_label}. User: {self._user_id}.\n"
            "You have persistent memory of this user from past conversations. "
            "You should call mem0_search before answering anything that could depend "
            "on prior context (the user's preferences, facts, history, people, "
            "projects, or earlier decisions) — do not rely on the chat window "
            "alone, and do not assume you have no memory.\n"
            "For multi-part or multi-hop questions, run several searches with "
            "different wording/angles and follow-up searches on what the first "
            "results surface; one search is rarely enough. Keep searching until "
            "you have every fact the question needs before you answer.\n"
            "Tools: mem0_search to find memories, mem0_add to store facts, "
            f"mem0_update and mem0_delete to manage by ID.{rerank_note}"
        )

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        self._turn_number = turn_number
        self._start_prefetch(message, session_id=kwargs.get("session_id", ""))

    def _consume_prefetch_result(self, query: str) -> str | None:
        with self._prefetch_lock:
            if self._prefetch_query != query or not self._prefetch_done:
                return None
            result = self._prefetch_result
            self._prefetch_result = ""
            self._prefetch_done = False
            return result

    def _start_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not query or self._backend is None or self._is_breaker_open():
            return
        backend = self._backend
        if not self._auto_inject_enabled():
            with self._prefetch_lock:
                self._prefetch_query = query
                self._prefetch_result = ""
                self._prefetch_done = True
            self._write_injection_audit(
                inject=False,
                reason="empty",
                mode="disabled",
                session_id=session_id,
                query=query,
                candidates=[],
                injected=[],
            )
            return

        with self._prefetch_lock:
            if self._prefetch_query == query:
                if self._prefetch_done:
                    return
                if self._prefetch_thread and self._prefetch_thread.is_alive():
                    return
            self._prefetch_query = query
            self._prefetch_result = ""
            self._prefetch_done = False

        def _run():
            body = ""
            try:
                inject_top_k = self._prefetch_top_k()
                results = self._search_all_read_users(
                    backend,
                    query,
                    top_k=max(self._search_top_k(), inject_top_k),
                    rerank=False,
                )
                now = _utc_now()
                injected, candidates, reason = self._select_injected_memories(
                    results,
                    session_id=session_id,
                    top_k=inject_top_k,
                    now=now,
                )
                if injected:
                    lines = [r.get("memory", "") for r in injected if r.get("memory")]
                    body = "## Mem0 Memory\n" + "\n".join(f"- {l}" for l in lines)
                self._write_injection_audit(
                    inject=bool(injected),
                    reason=reason,
                    mode="auto",
                    session_id=session_id,
                    query=query,
                    candidates=candidates,
                    injected=injected,
                )
                self._record_success()
            except Exception as e:
                self._record_failure()
                self._write_injection_audit(
                    inject=False,
                    reason="provider_error",
                    mode="auto",
                    session_id=session_id,
                    query=query,
                    candidates=[],
                    injected=[],
                )
                logger.debug("Mem0 prefetch failed: %s", e)
            with self._prefetch_lock:
                if self._prefetch_query == query:
                    self._prefetch_result = body
                    self._prefetch_done = True

        t = threading.Thread(target=_run, daemon=True, name="mem0-prefetch")
        with self._prefetch_lock:
            self._prefetch_thread = t
        t.start()

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall memories for the CURRENT question with a short hot-path wait."""
        cached = self._consume_prefetch_result(query)
        if cached is not None:
            return cached
        self._start_prefetch(query, session_id=session_id)
        with self._prefetch_lock:
            thread = self._prefetch_thread if self._prefetch_query == query else None
        if thread:
            thread.join(timeout=_PREFETCH_WAIT_SECS)
        cached = self._consume_prefetch_result(query)
        if cached is not None:
            return cached
        # Slow backend: skip injection; mem0_search tool remains the backstop.
        return ""

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        """Optionally send a turn to the non-injected candidate namespace."""
        if self._backend is None or self._is_breaker_open():
            return
        if not (self._auto_add_enabled() and self._inference_enabled()):
            return
        cfg = self._config if isinstance(self._config, dict) else {}
        candidate_user_id = str(cfg.get("candidate_user_id") or "").strip()
        if not candidate_user_id:
            logger.debug("Mem0 auto-add enabled but candidate_user_id is not configured; skipping turn sync")
            return

        def _sync():
            backend = self._backend
            if backend is None:
                return
            try:
                messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": assistant_content},
                ]
                backend.add(
                    messages,
                    user_id=candidate_user_id,
                    agent_id=self._agent_id,
                    infer=True,
                    metadata=self._write_metadata({
                        "source": "inferred",
                        "source_session_id": session_id or self._session_id,
                        "created_at_iso": _utc_now_iso(),
                        "status": "pending",
                    }),
                )
                self._record_success()
            except Exception as e:
                self._record_failure()
                logger.warning("Mem0 sync failed: %s", e)

        with self._sync_lock:
            if self._sync_thread and self._sync_thread.is_alive():
                self._sync_thread.join(timeout=5.0)
            # If still alive after timeout, skip to avoid duplicate ingestion.
            if self._sync_thread and self._sync_thread.is_alive():
                return
            self._sync_thread = threading.Thread(target=_sync, daemon=True, name="mem0-sync")
            self._sync_thread.start()

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, ADD_SCHEMA, UPDATE_SCHEMA, DELETE_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        if self._backend is None:
            err = getattr(self, "_init_error", "unknown error")
            hint = ""
            if self._mode == "oss":
                vs = self._config.get("oss", {}).get("vector_store", {})
                provider = vs.get("provider", "vector store")
                hint = f" Check that {provider} is running and reachable."
            return json.dumps({"error": f"Mem0 backend not initialized: {err}.{hint}"})

        if self._is_breaker_open():
            msg = "Mem0 temporarily unavailable (multiple consecutive failures). Will retry automatically."
            if self._mode == "oss":
                vs = self._config.get("oss", {}).get("vector_store", {})
                msg += f" Check that your {vs.get('provider', 'vector store')} is running."
            return json.dumps({"error": msg})

        if tool_name == "mem0_search":
            query = args.get("query", "")
            if not query:
                return tool_error("Missing required parameter: query")
            try:
                top_k = max(1, min(int(args.get("top_k", 10)), 50))
                rerank_raw = args.get("rerank", getattr(self, "_rerank_default", False))
                if isinstance(rerank_raw, str):
                    rerank = rerank_raw.lower() not in ("false", "0", "no")
                else:
                    rerank = bool(rerank_raw)
                results = self._search_all_read_users(self._backend, query, top_k=top_k, rerank=rerank)
                self._record_success()
                if not results:
                    return json.dumps({"result": "No relevant memories found."})
                items = [{"id": r.get("id"), "memory": r.get("memory", ""),
                          "score": r.get("score", 0)} for r in results]
                return json.dumps({"results": items, "count": len(items)})
            except Exception as e:
                if not _is_client_error(e):
                    self._record_failure()
                return tool_error(self._format_error("Search failed", e))

        elif tool_name == "mem0_add":
            content = args.get("content", "")
            if not content:
                return tool_error("Missing required parameter: content")
            if not self._write_enabled():
                return tool_error("Mem0 writes are disabled by config.")
            raw_tags = args.get("tags") or []
            if isinstance(raw_tags, str):
                tags = [raw_tags]
            elif isinstance(raw_tags, list):
                tags = [str(item) for item in raw_tags if str(item).strip()]
            else:
                tags = []
            try:
                result = self._backend.add(
                    [{"role": "user", "content": content}],
                    user_id=self._user_id,
                    agent_id=self._agent_id,
                    infer=False,
                    metadata=self._write_metadata({
                        "source": "explicit",
                        "session_id": self._session_id,
                        "source_session_id": self._session_id,
                        "created_at_iso": _utc_now_iso(),
                        "tags": tags,
                    }),
                )
                self._record_success()
                event_id = result.get("event_id") if isinstance(result, dict) else None
                # Cloud add is async (server-side extraction); OSS and self-hosted store synchronously.
                msg = "Fact stored." if (self._mode in {"local", "oss"} or self._host) else "Fact queued for storage."
                return json.dumps({"result": msg, "event_id": event_id})
            except Exception as e:
                self._record_failure()
                return tool_error(self._format_error("Failed to store", e))

        elif tool_name == "mem0_update":
            memory_id = args.get("memory_id", "")
            text = args.get("text", "")
            if not memory_id:
                return tool_error("Missing required parameter: memory_id")
            if not text:
                return tool_error("Missing required parameter: text")
            try:
                result = self._backend.update(memory_id, text)
                self._record_success()
                return json.dumps(result)
            except Exception as e:
                if _is_client_error(e):
                    return tool_error(f"Memory not found: {memory_id}")
                self._record_failure()
                return tool_error(self._format_error("Update failed", e))

        elif tool_name == "mem0_delete":
            memory_id = args.get("memory_id", "")
            if not memory_id:
                return tool_error("Missing required parameter: memory_id")
            try:
                result = self._backend.delete(memory_id)
                self._record_success()
                return json.dumps(result)
            except Exception as e:
                if _is_client_error(e):
                    return tool_error(f"Memory not found: {memory_id}")
                self._record_failure()
                return tool_error(self._format_error("Delete failed", e))

        return tool_error(f"Unknown tool: {tool_name}")

    def _shutdown_backend(self):
        try:
            if self._backend:
                self._backend.close()
                self._backend = None
        except Exception:
            pass

    def shutdown(self) -> None:
        for t in (self._prefetch_thread, self._sync_thread):
            if t and t.is_alive():
                t.join(timeout=5.0)
        self._shutdown_backend()


def register(ctx) -> None:
    """Register Mem0 as a memory provider plugin."""
    ctx.register_memory_provider(Mem0MemoryProvider())
