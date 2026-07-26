#!/usr/bin/env python3
"""Import distilled ChatGPT/Claude-style export memory into Hindsight.

This intentionally imports the explicit memory blob plus conversation summaries.
It does not import raw chat_messages by default; those are noisy, large, and
better handled by a separate archival pipeline if needed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_API_URL = "http://127.0.0.1:9177"
DEFAULT_BANK_ID = "mara-hindsight"
DEFAULT_TAGS = ["mara", "import:export", "source:chatgpt"]


def _load_hindsight_config(hermes_home: Path) -> dict[str, Any]:
    config_path = hermes_home / "hindsight" / "config.json"
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _redact(text: str, *, enabled: bool) -> str:
    if not enabled:
        return text
    try:
        from agent.redact import redact_sensitive_text

        return redact_sensitive_text(text, force=True)
    except Exception:
        return text


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _metadata(**values: Any) -> dict[str, str]:
    return {
        key: str(value)
        for key, value in values.items()
        if value is not None and str(value) != ""
    }


def build_import_items(export_dir: Path, *, redact: bool = True) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    memories_path = export_dir / "memories.json"
    if memories_path.exists():
        memories = _read_json(memories_path)
        if isinstance(memories, list):
            for index, record in enumerate(memories, 1):
                if not isinstance(record, dict):
                    continue
                content = _text(record.get("conversations_memory"))
                if not content:
                    continue
                items.append(
                    {
                        "document_id": f"imported-memory-{index}",
                        "content": _redact(
                            "Imported distilled memory export.\n\n" + content,
                            enabled=redact,
                        ),
                        "context": "imported distilled user memory",
                        "metadata": _metadata(
                            source="chatgpt_export_memory",
                            account_uuid=record.get("account_uuid"),
                            import_file="memories.json",
                            import_index=index,
                        ),
                        "tags": DEFAULT_TAGS + ["memory"],
                    }
                )

    conversations_path = export_dir / "conversations.json"
    if conversations_path.exists():
        conversations = _read_json(conversations_path)
        if isinstance(conversations, list):
            for index, convo in enumerate(conversations, 1):
                if not isinstance(convo, dict):
                    continue
                summary = _text(convo.get("summary"))
                if not summary:
                    continue
                uuid = _text(convo.get("uuid")) or str(index)
                name = _text(convo.get("name")) or "Untitled conversation"
                content = (
                    "Imported conversation summary.\n\n"
                    f"Title: {name}\n"
                    f"Created: {_text(convo.get('created_at'))}\n"
                    f"Updated: {_text(convo.get('updated_at'))}\n\n"
                    f"{summary}"
                )
                items.append(
                    {
                        "document_id": f"imported-conversation-summary-{uuid}",
                        "content": _redact(content, enabled=redact),
                        "context": "imported conversation summary",
                        "metadata": _metadata(
                            source="chatgpt_export_conversation_summary",
                            conversation_uuid=uuid,
                            conversation_name=name,
                            created_at=convo.get("created_at"),
                            updated_at=convo.get("updated_at"),
                            import_file="conversations.json",
                            import_index=index,
                        ),
                        "tags": DEFAULT_TAGS + ["conversation-summary"],
                    }
                )

    return items


async def _retain_items(
    items: list[dict[str, Any]],
    *,
    api_url: str,
    api_key: str,
    bank_id: str,
    timeout: float,
) -> None:
    from hindsight_client import Hindsight

    kwargs: dict[str, Any] = {"base_url": api_url, "timeout": timeout}
    if api_key:
        kwargs["api_key"] = api_key
    client = Hindsight(**kwargs)
    try:
        for item in items:
            document_id = item.pop("document_id")
            await client.aretain_batch(
                bank_id=bank_id,
                items=[item],
                document_id=document_id,
                retain_async=True,
            )
    finally:
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import distilled export memory and summaries into Hindsight."
    )
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--hermes-home", type=Path, default=Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")))
    parser.add_argument("--api-url")
    parser.add_argument("--api-key", default=os.environ.get("HINDSIGHT_API_KEY", ""))
    parser.add_argument("--bank-id")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-redact", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    config = _load_hindsight_config(args.hermes_home)
    api_url = args.api_url or config.get("api_url") or DEFAULT_API_URL
    api_key = args.api_key or config.get("apiKey") or config.get("api_key") or ""
    bank_id = args.bank_id or config.get("bank_id") or DEFAULT_BANK_ID

    items = build_import_items(args.export_dir, redact=not args.no_redact)
    if args.limit > 0:
        items = items[: args.limit]

    memory_count = sum(1 for item in items if "memory" in item.get("tags", []))
    summary_count = sum(
        1 for item in items if "conversation-summary" in item.get("tags", [])
    )
    print(
        f"Prepared {len(items)} Hindsight retain item(s): "
        f"{memory_count} memory, {summary_count} conversation summaries."
    )
    print(f"Target: {api_url} bank={bank_id}")

    if args.dry_run:
        return 0

    asyncio.run(
        _retain_items(
            items,
            api_url=api_url,
            api_key=api_key,
            bank_id=bank_id,
            timeout=args.timeout,
        )
    )
    print("Import complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
