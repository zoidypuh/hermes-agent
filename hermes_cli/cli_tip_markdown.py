"""Terminal-only styling for the shared three-line learning tip."""

import re

from rich.markdown import CodeBlock, Markdown
from rich.syntax import Syntax


class TipCodeBlock(CodeBlock):
    """Keep portable tip Markdown intact and style its terminal presentation."""

    def __rich_console__(self, console, options):
        code = str(self.text).rstrip()
        lines = code.splitlines()
        is_tip = (
            self.lexer_name == "text"
            and len(lines) == 3
            and (lines[0] == "=====" or re.fullmatch(r"=== Tip für .+ ===", lines[0]))
            and all(line.strip() for line in lines[1:])
        )
        if not is_tip:
            yield from super().__rich_console__(console, options)
            return
        yield Syntax(
            code, "text", theme=self.theme, word_wrap=True, padding=1,
            background_color="#10243a",
        )


class TerminalMarkdown(Markdown):
    # Copy the dispatch map: changing Rich's global map would affect other UIs.
    elements = {**Markdown.elements, "fence": TipCodeBlock}
