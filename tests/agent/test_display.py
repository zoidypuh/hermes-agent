"""Tests for agent/display.py — build_tool_preview() and inline diff previews."""

import json
import re
import pytest
from unittest.mock import MagicMock

import agent.display as display_module
from agent.display import (
    build_tool_preview,
    capture_local_edit_snapshot,
    extract_edit_diff,
    get_cute_tool_message,
    prepare_tool_preview,
    redact_tool_args_for_display,
    set_tool_preview_max_len,
    set_tool_preview_mode,
    _render_inline_unified_diff,
    _summarize_rendered_diff_sections,
    render_edit_diff_with_delta,
)


@pytest.fixture(autouse=True)
def reset_tool_preview_max_len():
    set_tool_preview_max_len(0)
    set_tool_preview_mode("preview")
    yield
    set_tool_preview_max_len(0)
    set_tool_preview_mode("preview")


def test_name_only_mode_hides_tool_arguments_in_progress_and_completion():
    secret_command = "python3 -c 'print(12345)'"
    set_tool_preview_mode("name_only")

    assert display_module.build_tool_preview("terminal", {"command": secret_command}) is None
    line = get_cute_tool_message("terminal", {"command": secret_command}, 3.0)
    assert "terminal" in line
    assert "python3" not in line
    assert "12345" not in line


def _strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)


def _expected_token_label(result: str) -> str:
    from agent.model_metadata import estimate_tokens_rough
    from agent.usage_pricing import format_token_count_compact
    return f"{format_token_count_compact(estimate_tokens_rough(result))} tok"


def _expected_total_label(results: list[str]) -> str:
    from agent.model_metadata import estimate_tokens_rough
    from agent.usage_pricing import format_token_count_compact
    total = sum(estimate_tokens_rough(r) for r in results)
    return f"∑ {format_token_count_compact(total)} tok total"


def test_completion_line_has_no_total_line():
    first = json.dumps({"output": "hello world " * 50, "exit_code": 0})
    first_line = get_cute_tool_message("terminal", {"command": "echo hi"}, 1.2, result=first)
    assert first_line.count("\n") == 0
    assert "∑" not in _strip_ansi(first_line)


def test_turn_total_line_sums_all_calls():
    import json as _json
    from agent.display import tool_token_total_line, turn_tool_token_total
    first = _json.dumps({"output": "hello world " * 50, "exit_code": 0})
    second = _json.dumps({"output": "boom " * 80, "exit_code": 0})

    messages = [
        {"role": "tool", "content": first},
        {"role": "tool", "content": second},
    ]
    total = turn_tool_token_total(messages)
    total_line = tool_token_total_line(total)
    assert _strip_ansi(total_line).strip().endswith(_expected_total_label([first, second]))
    assert "\033[38;2;239;83;80m" in total_line or "\033[38;2;" in total_line


def test_token_usage_follows_duration_in_orange():
    result = json.dumps({"output": "hello world " * 50, "exit_code": 0})
    line = get_cute_tool_message("terminal", {"command": "echo hi"}, 1.2, result=result)
    label = _expected_token_label(result)

    first_line = line.splitlines()[0]
    after_duration = first_line.split("1.2s", 1)[1]
    assert after_duration.startswith(" ")
    assert "\033[38;2;" in after_duration
    assert _strip_ansi(after_duration).strip() == label
    assert "[" not in _strip_ansi(after_duration)


def test_token_usage_follows_exit_suffix_in_orange():
    result = json.dumps({"output": "boom " * 80, "exit_code": 2})
    line = get_cute_tool_message("terminal", {"command": "false"}, 0.0, result=result)
    label = _expected_token_label(result)

    plain = _strip_ansi(line.splitlines()[0])
    assert f"0.0s [exit 2] {label}" in plain
    assert "\033[38;2;" in line.split("[exit 2]", 1)[1]


def test_name_only_token_usage_follows_exit_suffix():
    set_tool_preview_mode("name_only")
    result = json.dumps({"output": "boom " * 80, "exit_code": 2})
    line = get_cute_tool_message("terminal", {"command": "false"}, 0.0, result=result)
    label = _expected_token_label(result)
    assert f"terminal  0.0s [exit 2] {label}" in _strip_ansi(line.splitlines()[0])


def test_cute_tool_message_falls_back_when_renderer_raises(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("cosmetic failure")

    monkeypatch.setattr(display_module, "_get_cute_tool_message", _boom)

    assert get_cute_tool_message("web_extract", {"urls": []}, 0.25) == (
        "┊ ⚡ web_extra completed  0.2s"
    )


class TestBuildToolPreview:
    """Tests for build_tool_preview defensive handling and normal operation."""

    def test_none_args_returns_none(self):
        """PR #453: None args should not crash, should return None."""
        assert build_tool_preview("terminal", None) is None

    def test_empty_dict_returns_none(self):
        """Empty dict has no keys to preview."""
        assert build_tool_preview("terminal", {}) is None








    def test_browser_type_preview_redacts_api_key(self):
        secret = "sk-proj-ABCD1234567890EFGH"
        result = build_tool_preview("browser_type", {"ref": "@e3", "text": secret})
        assert result is not None
        assert secret not in result
        assert "sk-pro" in result and "..." in result

    def test_browser_type_preview_keeps_normal_text(self):
        text = "hello world search query"
        result = build_tool_preview("browser_type", {"ref": "@e3", "text": text})
        assert result is not None
        assert text in result

    def test_browser_type_display_args_redact_api_key(self):
        secret = "ghp_ABCDEFGHIJ1234567890"
        safe_args = redact_tool_args_for_display(
            "browser_type", {"ref": "@e3", "text": secret}
        )
        assert secret not in str(safe_args)
        assert safe_args["ref"] == "@e3"
        assert safe_args["text"].startswith("ghp_AB")















    def test_delegate_task_batch_preview_respects_max_len(self):
        result = build_tool_preview(
            "delegate_task",
            {"tasks": [{"goal": "A" * 80}, {"goal": "B" * 80}]},
            max_len=30,
        )
        assert result == "2 tasks: AAAAAAAAAAAAAAAAAA..."
        assert len(result) == 30

    def test_false_like_args_zero(self):
        """Non-dict falsy values should return None, not crash."""
        assert build_tool_preview("terminal", 0) is None
        assert build_tool_preview("terminal", "") is None
        assert build_tool_preview("terminal", []) is None


class TestPrepareToolPreview:
    def test_recovers_and_describes_truncated_url(self):
        url = "https://example.com/a/very/long/path/to/a/page"
        set_tool_preview_max_len(20)

        preview = prepare_tool_preview(
            "web_extract",
            {"urls": [url]},
            fallback=url[:17] + "...",
            max_len=20,
        )

        assert preview.text == url[:17] + "..."
        assert preview.truncated is True
        assert preview.url == url

    def test_untruncated_url_has_no_link_target(self):
        url = "https://example.com/page"
        preview = prepare_tool_preview(
            "browser_navigate", None, fallback=url, max_len=40
        )

        assert preview.text == url
        assert preview.truncated is False
        assert preview.url is None

    def test_truncated_non_url_has_no_link_target(self):
        preview = prepare_tool_preview(
            "web_search",
            {"query": "how to parse a URL"},
            fallback="how to parse a URL",
            max_len=12,
        )

        assert preview.truncated is True
        assert preview.url is None


class TestCuteToolMessagePreviewLength:


    def test_search_files_preview_uses_positive_configured_limit_not_default(self):
        set_tool_preview_max_len(80)
        pattern = "function.formatToolCall.context.preview.compactPreview.maxLength.truncate"

        line = get_cute_tool_message("search_files", {"pattern": pattern}, 0.1)

        assert pattern in line
        assert "..." not in line





    def test_browser_type_cute_message_redacts_api_key(self):
        secret = "sk-proj-ABCD1234567890EFGH"
        line = get_cute_tool_message(
            "browser_type",
            {"ref": "@password", "text": secret},
            0.1,
            result='{"success": true, "typed": "sk-pro...EFGH"}',
        )

        assert secret not in line
        assert "sk-pro" in line

    def test_browser_type_cute_message_keeps_normal_text(self):
        text = "hello world"
        line = get_cute_tool_message(
            "browser_type",
            {"ref": "@search", "text": text},
            0.1,
            result='{"success": true, "typed": "hello world"}',
        )

        assert text in line


class TestEditDiffPreview:



    def test_extract_edit_diff_uses_local_snapshot_for_write_file(self, tmp_path):
        target = tmp_path / "note.txt"
        target.write_text("old\n", encoding="utf-8")

        snapshot = capture_local_edit_snapshot("write_file", {"path": str(target)})

        target.write_text("new\n", encoding="utf-8")

        diff = extract_edit_diff(
            "write_file",
            '{"bytes_written": 4}',
            function_args={"path": str(target)},
            snapshot=snapshot,
        )

        assert diff is not None
        assert "--- a/" in diff
        assert "+++ b/" in diff
        assert "-old" in diff
        assert "+new" in diff



    def test_render_edit_diff_with_delta_handles_renderer_errors(self, monkeypatch):
        printer = MagicMock()

        monkeypatch.setattr("agent.display._summarize_rendered_diff_sections", MagicMock(side_effect=RuntimeError("boom")))

        rendered = render_edit_diff_with_delta(
            "patch",
            '{"diff": "--- a/x\\n+++ b/x\\n"}',
            print_fn=printer,
        )

        assert rendered is False
        assert printer.call_count == 0


    def test_summarize_rendered_diff_sections_limits_file_count(self):
        diff = "".join(
            f"--- a/file{i}.py\n+++ b/file{i}.py\n+line{i}\n"
            for i in range(8)
        )

        rendered = _summarize_rendered_diff_sections(diff, max_files=3, max_lines=50)

        assert any("a/file0.py" in line for line in rendered)
        assert any("a/file1.py" in line for line in rendered)
        assert any("a/file2.py" in line for line in rendered)
        assert not any("a/file7.py" in line for line in rendered)
        assert "additional file" in rendered[-1]


class TestBuildToolLabel:
    """Friendly human-phrased tool labels for built-in tools."""

    @pytest.fixture(autouse=True)
    def _enable_friendly(self):
        from agent.display import set_friendly_tool_labels
        set_friendly_tool_labels(True)
        yield
        set_friendly_tool_labels(True)

    def test_web_search_uses_for_connector(self):
        from agent.display import build_tool_label
        label = build_tool_label("web_search", {"query": "weather in NYC"})
        assert label == 'Searching the web for weather in NYC'

    def test_web_extract_reads_url(self):
        from agent.display import build_tool_label
        label = build_tool_label("web_extract", {"urls": ["https://example.com/page"]})
        assert label is not None
        assert label.startswith("Reading ")
        assert "example.com/page" in label







    def test_disabled_falls_back_to_preview(self):
        from agent.display import (
            build_tool_label,
            build_tool_preview,
            set_friendly_tool_labels,
        )
        set_friendly_tool_labels(False)
        args = {"query": "weather in NYC"}
        label = build_tool_label("web_search", args)
        # With the feature off, must match the raw preview exactly
        assert label == build_tool_preview("web_search", args)
        assert "Searching the web" not in (label or "")



class TestBuildStatusPhrase:
    """build_status_phrase — live working-state text for Slack's status line."""



    def test_verb_only_when_args_none(self):
        # live_status: "verb" mode passes args=None to suppress previews.
        from agent.display import build_status_phrase
        assert build_status_phrase("terminal", None) == "is running…"
        assert build_status_phrase("read_file", None) == "is reading…"



    def test_caps_length_for_slack_status_line(self):
        from agent.display import build_status_phrase
        phrase = build_status_phrase(
            "terminal", {"command": "x" * 300}, max_len=49
        )
        assert phrase is not None and len(phrase) <= 49
        assert phrase.endswith("…")


    def test_respects_friendly_labels_toggle(self):
        from agent.display import build_status_phrase, set_friendly_tool_labels
        set_friendly_tool_labels(False)
        try:
            assert build_status_phrase("terminal", {"command": "ls"}) is None
        finally:
            set_friendly_tool_labels(True)
