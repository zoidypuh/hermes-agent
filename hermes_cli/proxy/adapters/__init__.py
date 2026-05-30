"""Upstream adapter registry for the local proxy server.

Each adapter wraps a provider's OAuth state and exposes a uniform interface
the proxy server can use to forward requests with a freshly-minted bearer
token. See :class:`UpstreamAdapter` for the contract.
"""

from typing import Dict, Type

from hermes_cli.proxy.adapters.base import UpstreamAdapter
from hermes_cli.proxy.adapters.nous_portal import NousPortalAdapter
from hermes_cli.proxy.adapters.xai import XAIGrokAdapter

# Registry of available adapter classes keyed by provider name as used on
# the ``hermes proxy start --provider <name>`` CLI flag.
ADAPTERS: Dict[str, Type[UpstreamAdapter]] = {
    "nous": NousPortalAdapter,
    "xai-oauth": XAIGrokAdapter,
}

_ADAPTER_ALIASES: Dict[str, str] = {
    "grok-oauth": "xai-oauth",
    "x-ai-oauth": "xai-oauth",
    "xai": "xai-oauth",
    "xai-grok-oauth": "xai-oauth",
}


def get_adapter(name: str) -> UpstreamAdapter:
    """Instantiate an adapter by provider name.

    Raises:
        ValueError: if ``name`` is not a registered adapter.
    """
    key = (name or "").strip().lower()
    key = _ADAPTER_ALIASES.get(key, key)
    if key not in ADAPTERS:
        available_names = sorted(set(ADAPTERS) | set(_ADAPTER_ALIASES))
        available = ", ".join(available_names) or "(none)"
        raise ValueError(
            f"Unknown proxy upstream provider: {name!r}. Available: {available}"
        )
    return ADAPTERS[key]()


__all__ = ["UpstreamAdapter", "ADAPTERS", "get_adapter"]
