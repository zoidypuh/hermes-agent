"""xAI OAuth upstream adapter.

Reads the user's xAI OAuth state from ``~/.hermes/auth.json``, refreshes the
access token through the shared xAI OAuth runtime helper when needed, and
exposes the xAI REST API base URL plus bearer for the proxy server to forward
to.
"""

from __future__ import annotations

import threading
from typing import FrozenSet

from hermes_cli.auth import (
    DEFAULT_XAI_OAUTH_BASE_URL,
    _read_xai_oauth_tokens,
    resolve_xai_oauth_runtime_credentials,
)
from hermes_cli.proxy.adapters.base import UpstreamAdapter, UpstreamCredential

_ALLOWED_PATHS: FrozenSet[str] = frozenset(
    {
        "/chat/completions",
        "/chat/deferred-completion",
        "/complete",
        "/completions",
        "/image-generation-models",
        "/images/generations",
        "/language-models",
        "/messages",
        "/models",
        "/responses",
        "/tokenize-text",
    }
)

_ALLOWED_PREFIXES: FrozenSet[str] = frozenset(
    {
        "/chat/deferred-completion/",
        "/image-generation-models/",
        "/language-models/",
        "/models/",
        "/responses/",
    }
)


class XaiOAuthAdapter(UpstreamAdapter):
    """Proxy upstream for xAI's OpenAI-compatible REST API."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "xai-oauth"

    @property
    def display_name(self) -> str:
        return "xAI Grok OAuth"

    @property
    def allowed_paths(self) -> FrozenSet[str]:
        return _ALLOWED_PATHS

    def is_path_allowed(self, rel_path: str) -> bool:
        return rel_path in _ALLOWED_PATHS or any(
            rel_path.startswith(prefix) for prefix in _ALLOWED_PREFIXES
        )

    def is_authenticated(self) -> bool:
        try:
            _read_xai_oauth_tokens()
            return True
        except Exception:
            return False

    def get_credential(self) -> UpstreamCredential:
        with self._lock:
            try:
                creds = resolve_xai_oauth_runtime_credentials()
            except Exception as exc:
                raise RuntimeError(
                    "Failed to resolve xAI OAuth credentials. "
                    "Run `hermes login xai-oauth` or select xAI OAuth in `hermes model` "
                    f"to re-authenticate. Error: {exc}"
                ) from exc

            bearer = str(creds.get("api_key", "") or "").strip()
            if not bearer:
                raise RuntimeError(
                    "xAI OAuth credential resolution did not return an access token. "
                    "Try `hermes login xai-oauth` to re-authenticate."
                )
            base_url = (
                str(creds.get("base_url", "") or "").strip().rstrip("/")
                or DEFAULT_XAI_OAUTH_BASE_URL
            )
            return UpstreamCredential(
                bearer=bearer,
                base_url=base_url,
            )


__all__ = ["XaiOAuthAdapter"]
