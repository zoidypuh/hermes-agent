"""Reroute /voice mode: skip TTS and instruct the model to POST replies."""

from __future__ import annotations

import os
from pathlib import Path

ASSISTANT_REPLY_URL = "http://100.79.30.18:8768/api/assistant-reply"


def resolve_agent_id() -> str:
    """Use the running agent/profile name as assistant-reply ``agent_id``."""
    for key in ("HERMES_PROFILE_NAME", "HERMES_PROFILE"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    try:
        from hermes_constants import get_hermes_home

        active = Path(get_hermes_home()).parent.parent / "active_profile"
        # profiles/<name> → parent is profiles, grandparent is hermes root
        home = Path(get_hermes_home())
        if home.name and home.parent.name == "profiles":
            return home.name
        marker = home / "active_profile"
        if marker.is_file():
            name = marker.read_text(encoding="utf-8").strip()
            if name:
                return name
        if active.is_file():
            name = active.read_text(encoding="utf-8").strip()
            if name:
                return name
    except Exception:
        pass
    try:
        from hermes_cli.skin_engine import get_active_skin

        skin = get_active_skin()
        branding = getattr(skin, "branding", None) or {}
        if isinstance(branding, dict):
            name = str(branding.get("agent_name") or "").strip()
            if name:
                return name.lower().replace(" ", "-")
    except Exception:
        pass
    return "hermes"


def voice_reroute_prefix(agent_id: str | None = None) -> str:
    """Instruction prepended to every user prompt while /voice is on."""
    name = (agent_id or resolve_agent_id()).strip() or "hermes"
    return (
        "remember to send your answer as text over:\n"
        "{\n"
        '  "command": "curl -s -X POST '
        f"{ASSISTANT_REPLY_URL} "
        r'\\\n'
        r'  -H \"Content-Type: application/json\" '
        r'\\\n'
        "  -d '{\\n"
        f'    \\"agent_id\\": \\"{name}\\",\\n'
        '    \\"text\\": \\"<YOUR_ANSWER_TEXT>\\"\\n'
        "  }'\"\n"
        "}\n\n"
        "Replace <YOUR_ANSWER_TEXT> with your spoken-style reply. "
        "Call this via the terminal tool after you have the answer. "
        "The user prompt follows.\n\n"
    )


def prepend_voice_reroute(message, agent_id: str | None = None):
    """Prepend the reroute instruction to a string or multimodal list."""
    prefix = voice_reroute_prefix(agent_id)
    if isinstance(message, str):
        return prefix + message
    if isinstance(message, list):
        parts = list(message)
        for i, part in enumerate(parts):
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                updated = dict(part)
                updated["text"] = prefix + part["text"]
                parts[i] = updated
                return parts
        return [{"type": "text", "text": prefix}] + parts
    return message
