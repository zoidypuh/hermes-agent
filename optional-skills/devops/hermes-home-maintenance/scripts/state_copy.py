"""Directory-content rsync arguments for profile state handoffs."""

from __future__ import annotations

import os
from collections.abc import Iterable


def directory_copy_command(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    exclude: Iterable[str] = (),
    delete: bool = False,
    ignore_existing: bool = False,
    owner: str | None = None,
) -> list[str]:
    """Copy contents on every invocation, including into an existing directory.

    Exclusions are also protected from deletion by rsync. In particular, callers
    can exclude a historical nested state directory without deleting its recovery
    contents when refreshing the active top-level state.
    """
    source_text = os.fspath(source)
    destination_text = os.fspath(destination)
    if not source_text or not destination_text:
        raise ValueError("source and destination must identify directories")
    command = ["rsync", "-a", "--protect-args"]
    if delete:
        command.append("--delete")
    if ignore_existing:
        command.append("--ignore-existing")
    if owner:
        command.append(f"--chown={owner}")
    command.extend(f"--exclude={pattern}" for pattern in exclude)
    command.extend(["--", source_text.rstrip("/") + "/", destination_text.rstrip("/") + "/"])
    return command
