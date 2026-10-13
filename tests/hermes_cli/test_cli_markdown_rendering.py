from io import StringIO

from rich.console import Console, Group
from rich.markdown import Markdown

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


def test_terminal_tip_background_covers_wrapped_text_without_bleeding(monkeypatch):
    import cli

    command = 'find /home/gismar -type d -iname "*upnote*"'
    explanation = 'Sucht rekursiv nach Ordnern mit upnote im Namen, unabhängig von Groß- und Kleinschreibung.'
    message = f'Before the tip.\n\n```\n=====\n{command}\n{explanation}\n```\n\nAfter the tip.'
    for width in (24, 40, 80, 160):
        monkeypatch.setattr(cli, "_terminal_columns", lambda: width)
        console = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
        segments = list(console.render(_render_final_assistant_content(message)))
        tip_segments = [segment for segment in segments if segment.style and segment.style.bgcolor]
        assert tip_segments
        assert all(segment.style.bgcolor.get_truecolor() == (16, 36, 58) for segment in tip_segments)
        tip_text = ''.join(segment.text for segment in tip_segments)
        assert ''.join(tip_text.split()) == ''.join(('=====' + command + explanation).split())
        rendered_text = ''.join(segment.text for segment in segments)
        assert any(line.strip().startswith('find') for line in rendered_text.splitlines())
        assert any(line.strip().startswith('Sucht') for line in rendered_text.splitlines())
        surrounding = [segment for segment in segments if 'Before' in segment.text or 'After' in segment.text]
        assert surrounding
        assert all(not (segment.style and segment.style.bgcolor) for segment in surrounding)


def test_terminal_tip_styling_preserves_other_markdown_code_blocks():
    console = Console(file=StringIO(), width=80, force_terminal=True, color_system="truecolor")
    for message in (
        '```\nprintf hello\nA normal code block.\n```',
        '```python\n=====\nprint("hello")\n# A labelled code block.\n```',
        '```\n=====\nOne line only.\n```',
        '```\n=====\ncommand\nexplanation\nExtra line.\n```',
        '=====\ncommand\nexplanation',
    ):
        expected = list(console.render(Markdown(message)))
        actual = list(console.render(_render_final_assistant_content(message)))
        assert actual == expected


def test_final_assistant_content_preserves_ansi_card_background_and_surrounding_markdown():
    card = "\x1b[103m\x1b[30m╭────╮\n│ tip│\n╰────╯\x1b[0m"
    renderable = _render_final_assistant_content("**Hello**\n\n" + card)
    assert isinstance(renderable, Group)
    output = StringIO()
    Console(file=output, width=80, force_terminal=True, color_system="standard").print(renderable)
    assert "Hello" in output.getvalue()
    assert "\x1b[30;103m" in output.getvalue()
    assert "tip" in output.getvalue()




def test_literal_box_card_keeps_rows_with_or_without_ansi(monkeypatch):
    import cli
    from rich.text import Text

    monkeypatch.setattr(cli, "_terminal_columns", lambda: 80)
    card_lines = [
        "╭──────────────────────────────────────────────╮",
        "│ DOPUS F5                                     │",
        "│ Lädt den aktuellen Ordner neu. Ctrl+F5 oder  │",
        "│ Shift+F5 baut veraltete Thumbnails           │",
        "│ zusätzlich neu.                              │",
        "╰──────────────────────────────────────────────╯",
    ]
    for mode in ("render", "strip", "raw"):
        for ansi in (False, True):
            for prefix in ("", "**Hello**\n\n"):
                # ANSI can be absent, repeated per row, or inherited by the body.
                card = "\n".join(card_lines)
                if ansi:
                    card = "\x1b[103m\x1b[93m" + card + "\x1b[0m"
                rendered = _render_final_assistant_content(prefix + card, mode=mode)
                output = _render_to_text(rendered)
                rows = [row.rstrip() for row in output.splitlines()]
                for expected in card_lines:
                    assert expected in rows, (mode, ansi, prefix, output)
                if ansi and mode != "strip":
                    parts = rendered.renderables if isinstance(rendered, Group) else [rendered]
                    assert any(isinstance(part, Text) and part.spans for part in parts)


def test_literal_box_does_not_swallow_surrounding_markdown_or_fences():
    card = "╭──────────╮\n│ literal  │\n╰──────────╯"
    rendered = _render_final_assistant_content("**Before**\n\n" + card + "\n\n**After**")
    assert isinstance(rendered, Group)
    output = _render_to_text(rendered)
    assert "Before" in output and "After" in output and "**" not in output
    assert "│ literal  │" in output.splitlines()
    # An unfinished box is ordinary Markdown, not a card claiming the rest of the reply.
    assert isinstance(_render_final_assistant_content("╭────╮\ntext"), Markdown)
    assert isinstance(_render_final_assistant_content("```text\n" + card + "\n```"), Markdown)


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
