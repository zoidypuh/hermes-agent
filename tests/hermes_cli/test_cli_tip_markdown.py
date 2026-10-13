"""Keep the named learning tip distinct without losing wrapped content."""

from io import StringIO

from rich.console import Console

from hermes_cli.cli_render import _render_final_assistant_content


def test_named_tip_background_preserves_text_and_stops_at_the_block(monkeypatch):
    import cli

    header = "=== Tip für TMUX ==="
    command = 'tmux capture-pane -p -t "work:0.0" | tee pane-output.txt'
    explanation = "Zeigt den Inhalt des Panes vollständig und speichert ihn in pane-output.txt."
    message = f"Before.\n\n```\n{header}\n{command}\n{explanation}\n```\n\nAfter."

    for width in (24, 40, 80, 160):
        monkeypatch.setattr(cli, "_terminal_columns", lambda: width)
        console = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
        segments = list(console.render(_render_final_assistant_content(message)))
        colored = [segment for segment in segments if segment.style and segment.style.bgcolor]
        assert colored
        assert all(segment.style.bgcolor.get_truecolor() == (16, 36, 58) for segment in colored)
        actual = "".join(segment.text for segment in colored)
        assert "".join(actual.split()) == "".join((header + command + explanation).split())
        surrounding = [segment for segment in segments if "Before." in segment.text or "After." in segment.text]
        assert len(surrounding) == 2
        assert all(not (segment.style and segment.style.bgcolor) for segment in surrounding)
