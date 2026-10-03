from io import StringIO

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel

import pytest


PLAIN_TIP_CARD = (
    "╭──────────────────────────────────────────────╮\n"
    "│ 🧠  DOPUS  Ctrl+T / Ctrl+W / Ctrl+Tab        │\n"
    "│ Ctrl+T neuer Tab, Ctrl+W schließt ihn.       │\n"
    "│ Ctrl+Tab zweimal pendelt zwischen den        │\n"
    "│ letzten zwei Tabs.                           │\n"
    "╰──────────────────────────────────────────────╯"
)


@pytest.mark.parametrize("width", [64, 80, 123])
@pytest.mark.parametrize("prefix", ["", "**Hello**\n\n", "TL;DR\n- verified\n"])
def test_plain_tip_card_keeps_separate_rows_inside_response_panel(width, prefix):
    renderable = _render_final_assistant_content(prefix + PLAIN_TIP_CARD)
    output = StringIO()
    Console(file=output, width=width, force_terminal=False, color_system=None).print(
        Panel(renderable, padding=(1, 1))
    )
    rows = output.getvalue().splitlines()
    for card_row in PLAIN_TIP_CARD.splitlines():
        assert any(card_row in row for row in rows), output.getvalue()
    if prefix:
        assert "**Hello**" not in output.getvalue()


def test_per_row_ansi_tip_card_keeps_every_row_and_background():
    card = "\n".join("\x1b[103m\x1b[30m" + row + "\x1b[0m" for row in PLAIN_TIP_CARD.splitlines())
    output = StringIO()
    Console(file=output, width=80, force_terminal=True, color_system="standard").print(
        Panel(_render_final_assistant_content("**Before**\n\n" + card + "\n\n**After**"))
    )
    from rich.text import Text
    rows = Text.from_ansi(output.getvalue()).plain.splitlines()
    for card_row in PLAIN_TIP_CARD.splitlines():
        assert any(card_row in row for row in rows), output.getvalue()
    assert "\x1b[30;103m" in output.getvalue()
    assert "**Before**" not in output.getvalue()
    assert "**After**" not in output.getvalue()


@pytest.mark.parametrize("content", [
    "╭────╮\nnot a card\n╰────╯",
    "╭────╮\n│ tip│",
    "```text\n" + PLAIN_TIP_CARD + "\n```",
])
def test_non_card_or_fenced_card_keeps_markdown_rendering(content):
    assert isinstance(_render_final_assistant_content(content), Markdown)

from cli import _render_final_assistant_content


def _render_to_text(renderable) -> str:
    buf = StringIO()
    Console(file=buf, width=80, force_terminal=False, color_system=None).print(renderable)
    return buf.getvalue()


def test_final_assistant_content_uses_markdown_renderable():
    renderable = _render_final_assistant_content("# Title\n\n- one\n- two")

    assert isinstance(renderable, Markdown)
    output = _render_to_text(renderable)
    assert "Title" in output
    assert "one" in output
    assert "two" in output


def test_final_assistant_content_preserves_ansi_card_background_and_surrounding_markdown():
    card = "\x1b[103m\x1b[30m╭────╮\n│ tip│\n╰────╯\x1b[0m"
    renderable = _render_final_assistant_content("**Hello**\n\n" + card)
    assert isinstance(renderable, Group)
    output = StringIO()
    Console(file=output, width=80, force_terminal=True, color_system="standard").print(renderable)
    assert "Hello" in output.getvalue()
    assert "\x1b[30;103m" in output.getvalue()
    assert "tip" in output.getvalue()




def test_final_assistant_content_keeps_non_path_markdown_escapes():
    renderable = _render_final_assistant_content(r"1\. Not an ordered list")

    output = _render_to_text(renderable)
    assert "1. Not an ordered list" in output
    assert r"1\." not in output






def test_strip_mode_preserves_lists():
    renderable = _render_final_assistant_content(
        "**Formatting**\n- Ran prettier\n- Files changed\n- Verified clean",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "- Ran prettier" in output
    assert "- Files changed" in output
    assert "- Verified clean" in output
    assert "**" not in output




def test_strip_mode_preserves_blockquotes():
    renderable = _render_final_assistant_content(
        "> This is quoted text\n> Another quoted line",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "> This is quoted" in output
    assert "> Another quoted" in output






def test_strip_mode_preserves_cron_asterisks_in_plain_text():
    renderable = _render_final_assistant_content("* * * * *", mode="strip")

    output = _render_to_text(renderable)
    assert "* * * * *" in output

    # Still treat the canonical 3-asterisk Markdown horizontal rule as decoration.
    renderable = _render_final_assistant_content("* * *", mode="strip")
    output = _render_to_text(renderable)
    assert "* * *" not in output




def test_strip_mode_preserves_intraword_underscores_in_snake_case_identifiers():
    renderable = _render_final_assistant_content(
        "Let me look at test_case_with_underscores and SOME_CONST "
        "then /tmp/snake_case_dir/file_with_name.py",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "test_case_with_underscores" in output
    assert "SOME_CONST" in output
    assert "snake_case_dir" in output
    assert "file_with_name" in output


def test_strip_mode_still_strips_boundary_underscore_emphasis():
    renderable = _render_final_assistant_content(
        "say _hi_ and __bold__ now",
        mode="strip",
    )

    output = _render_to_text(renderable)
    assert "say hi and bold now" in output
