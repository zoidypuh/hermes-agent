"""Hardening against classic-CLI prompt_toolkit redraw crashes (WSL+tmux).

Production dump (2026-08-11): native segfault in prompt_toolkit
``formatted_text/utils.py:split_lines`` during ``Application._redraw`` while a
long streaming session had an attached image under WSL+tmux. These unit tests
cover the Python-side guards that:

  - sanitize FormattedText fragments before they hit the renderer
  - retry ``_output_screen_diff`` / ``_redraw`` on corrupt paint state
  - floor invalidate rate and set safe env defaults under WSL/tmux
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

import cli as cli_mod


class TestSafePtFragments:
    def test_empty_and_none(self):
        assert cli_mod._safe_pt_fragments(None) == []
        assert cli_mod._safe_pt_fragments([]) == []

    def test_normal_pairs(self):
        frags = [("class:hint", "hi"), ("", "there")]
        assert cli_mod._safe_pt_fragments(frags) == frags

    def test_drops_none_text_and_coerces_style(self):
        frags = [(None, "ok"), ("x", None), (123, "y"), ("", b"bytes")]
        out = cli_mod._safe_pt_fragments(frags)
        assert out == [("", "ok"), ("123", "y"), ("", "bytes")]

    def test_plain_strings_become_pairs(self):
        assert cli_mod._safe_pt_fragments(["a", "", "b"]) == [("", "a"), ("", "b")]

    def test_caps_pathological_fragment(self):
        huge = "x" * 20000
        out = cli_mod._safe_pt_fragments([("s", huge)])
        assert len(out) == 1
        assert len(out[0][1]) == 8192

    def test_swallows_iteration_errors(self):
        class Boom:
            def __iter__(self):
                raise RuntimeError("nope")

        assert cli_mod._safe_pt_fragments(Boom()) == []


class TestOutputScreenDiffRetry:
    def test_retries_on_attribute_error_with_none_previous(self):
        calls = []

        def fake_osd(*args):
            calls.append(args)
            if len(calls) == 1:
                raise AttributeError("'cell' object has no attribute 'char'")
            return "ok"

        screen = MagicMock()
        screen.height = 10
        prev = MagicMock()
        prev.height = 5

        result = cli_mod._hermes_call_output_screen_diff(
            fake_osd,
            app=None,
            output=None,
            screen=screen,
            current_pos=None,
            color_depth=None,
            previous_screen=prev,
            last_style="x",
            is_done=False,
            full_screen=False,
            attrs_for_style_string=None,
            style_string_has_style=None,
            size=None,
            previous_width=80,
        )
        assert result == "ok"
        assert len(calls) == 2
        # second call forces first-paint path
        assert calls[1][5] is None  # previous_screen
        assert calls[1][6] is None  # last_style
        assert calls[1][12] == 0  # previous_width

    def test_retries_on_runtime_error(self):
        calls = []

        def fake_osd(*args):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("paint race")
            return "recovered"

        screen = MagicMock()
        screen.height = 3
        assert (
            cli_mod._hermes_call_output_screen_diff(
                fake_osd, None, None, screen, None, None, None, None, False, False, None, None, None, 0
            )
            == "recovered"
        )
        assert len(calls) == 2


class TestSafeApplicationRedraw:
    def test_redraw_retries_after_python_paint_error(self):
        class _R:
            def __init__(self):
                self._previous_screen = object()

            def reset(self):
                self.reset_called = True

        class _App:
            pass

        app = _App()
        app.renderer = _R()
        calls = {"n": 0}

        def boom_then_ok(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TypeError("bad fragment")
            return "painted"

        app._redraw = boom_then_ok
        cli_mod._install_safe_application_redraw(app)
        assert app._redraw() == "painted"
        assert calls["n"] == 2
        assert app.renderer._previous_screen is None

    def test_redraw_swallows_second_failure(self):
        class _App:
            pass

        app = _App()
        app.renderer = type("R", (), {"_previous_screen": object(), "reset": lambda self: None})()

        def always_fail(*a, **k):
            raise ValueError("still broken")

        app._redraw = always_fail
        cli_mod._install_safe_application_redraw(app)
        assert app._redraw() is None  # must not raise


class TestWslTmuxEnv:
    def test_detects_tmux(self, monkeypatch):
        monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.delenv("WSL_INTEROP", raising=False)
        assert cli_mod._running_under_wsl_or_tmux() is True

    def test_detects_wsl(self, monkeypatch):
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TMUX_PANE", raising=False)
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-24.04")
        assert cli_mod._running_under_wsl_or_tmux() is True

    def test_env_hardening_sets_defaults(self, monkeypatch):
        monkeypatch.setenv("TMUX", "1")
        monkeypatch.delenv("PROMPT_TOOLKIT_NO_CPR", raising=False)
        monkeypatch.delenv("PYTHONFAULTHANDLER", raising=False)
        monkeypatch.setenv("TERM", "dumb")
        cli_mod._apply_wsl_tmux_ptk_env_hardening()
        assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "1"
        assert os.environ["PYTHONFAULTHANDLER"] == "1"
        assert os.environ["TERM"] == "xterm-256color"

    def test_env_hardening_respects_existing(self, monkeypatch):
        monkeypatch.setenv("TMUX", "1")
        monkeypatch.setenv("PROMPT_TOOLKIT_NO_CPR", "0")
        monkeypatch.setenv("TERM", "screen-256color")
        cli_mod._apply_wsl_tmux_ptk_env_hardening()
        assert os.environ["PROMPT_TOOLKIT_NO_CPR"] == "0"
        assert os.environ["TERM"] == "screen-256color"


class TestInvalidateFloor:
    def test_wsl_tmux_floors_min_interval(self, monkeypatch):
        cli = object.__new__(cli_mod.HermesCLI)
        cli._resize_recovery_pending = False
        cli._last_invalidate = 0.0
        app = MagicMock()
        cli._app = app
        monkeypatch.setattr(cli_mod, "_running_under_wsl_or_tmux", lambda: True)
        monkeypatch.setattr(cli_mod.time, "monotonic", lambda: 100.0)
        cli._invalidate(min_interval=0.05)
        app.invalidate.assert_called_once()
        # second call within floor must be dropped
        monkeypatch.setattr(cli_mod.time, "monotonic", lambda: 100.2)
        cli._invalidate(min_interval=0.05)
        assert app.invalidate.call_count == 1
