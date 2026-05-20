from gateway.silent_response import SILENT_RESPONSE_SENTINEL, is_silent_response


def test_exact_silent_sentinel_suppresses_delivery():
    assert is_silent_response(SILENT_RESPONSE_SENTINEL)
    assert is_silent_response(f"\n  {SILENT_RESPONSE_SENTINEL}\t")


def test_mentions_of_silent_sentinel_are_not_suppressed():
    assert not is_silent_response(f"Reply with ONLY: {SILENT_RESPONSE_SENTINEL}")
    assert not is_silent_response(f"{SILENT_RESPONSE_SENTINEL} done")
    assert not is_silent_response("")
    assert not is_silent_response(None)
