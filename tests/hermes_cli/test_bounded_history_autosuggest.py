from types import SimpleNamespace

from prompt_toolkit.document import Document

from hermes_cli.commands import BoundedHistoryAutoSuggest


class _History:
    def __init__(self, loaded_strings):
        self._loaded_strings = loaded_strings
        self.get_strings_called = False

    def get_strings(self):
        self.get_strings_called = True
        return list(reversed(self._loaded_strings))


def test_bounded_history_autosuggest_scans_recent_loaded_entries_only():
    history = _History([
        "hello recent",
        "hello older",
    ])
    buffer = SimpleNamespace(history=history)

    suggestion = BoundedHistoryAutoSuggest(limit=1).get_suggestion(
        buffer,
        Document("hello"),
    )

    assert suggestion.text == " recent"
    assert history.get_strings_called is False


def test_bounded_history_autosuggest_respects_limit():
    history = _History([
        "skip this",
        "hello older",
    ])
    buffer = SimpleNamespace(history=history)

    suggestion = BoundedHistoryAutoSuggest(limit=1).get_suggestion(
        buffer,
        Document("hello"),
    )

    assert suggestion is None
    assert history.get_strings_called is False

