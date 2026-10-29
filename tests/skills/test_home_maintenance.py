"""Directory handoffs retain content semantics across repeated rsync calls."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "optional-skills/devops/hermes-home-maintenance/scripts/state_copy.py"
)
spec = importlib.util.spec_from_file_location("home_maintenance_state_copy", SCRIPT)
state_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state_copy)


@pytest.mark.platforms("linux", "macos")
def test_repeated_handoff_copies_contents_and_preserves_excluded_recovery(tmp_path):
    source = tmp_path / "source/session"
    destination = tmp_path / "destination/session"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "creds.json").write_text("first")
    (source / "session").mkdir()
    (source / "session/obsolete.json").write_text("nested source")
    (destination / "session").mkdir()
    (destination / "session/recover.json").write_text("unique recovery")
    (destination / "old.json").write_text("replaceable")
    for contents in ("first", "second"):
        (source / "creds.json").write_text(contents)
        subprocess.run(
            state_copy.directory_copy_command(
                source, destination, exclude=("/session/",), delete=True,
            ),
            check=True, capture_output=True,
        )
        assert (destination / "creds.json").read_text() == contents
        assert (destination / "session/recover.json").read_text() == "unique recovery"
        assert not (destination / "session/obsolete.json").exists()
        assert not (destination / "session/creds.json").exists()
        assert not (destination / "old.json").exists()


@pytest.mark.platforms("linux", "macos")
def test_transcript_copy_preserves_destination_history_and_ignores_nested_index(tmp_path):
    source = tmp_path / "source/sessions"
    destination = tmp_path / "destination/sessions"
    source.mkdir(parents=True)
    destination.mkdir(parents=True)
    (source / "sessions").mkdir()
    (source / "sessions/copied.jsonl").write_text("duplicate")
    (source / "sessions.json").write_text("source index")
    (source / "new.jsonl").write_text("new history")
    (destination / "old.jsonl").write_text("old history")
    (destination / "sessions.json").write_text("destination index")
    subprocess.run(
        state_copy.directory_copy_command(
            source, destination, exclude=("/sessions/", "/sessions.json"),
        ),
        check=True, capture_output=True,
    )
    assert (destination / "new.jsonl").read_text() == "new history"
    assert (destination / "old.jsonl").read_text() == "old history"
    assert (destination / "sessions.json").read_text() == "destination index"
    assert not (destination / "sessions").exists()
