"""Utilities for suppressing intentional no-op assistant replies."""

from __future__ import annotations

SILENT_RESPONSE_SENTINEL = "[SILENT]"


def is_silent_response(text: object) -> bool:
    """Return True when an assistant response explicitly requests no delivery.

    The match is intentionally narrow: only a response whose visible content is
    exactly ``[SILENT]`` after surrounding whitespace is suppressed.  This avoids
    eating normal prose that merely mentions the sentinel.
    """
    return isinstance(text, str) and text.strip() == SILENT_RESPONSE_SENTINEL
