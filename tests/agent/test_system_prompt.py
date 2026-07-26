"""Tests for agent/system_prompt.py — context-file cwd wiring."""

from types import SimpleNamespace
from unittest.mock import patch

from agent.system_prompt import build_system_prompt_parts


def _make_agent(**overrides):
    base = dict(
        load_soul_identity=False,
        skip_context_files=False,
        valid_tool_names=[],
        _task_completion_guidance=False,
        _tool_use_enforcement=False,
        _environment_probe=False,
        _kanban_worker_guidance="",
        _memory_store=None,
        _memory_manager=None,
        model="",
        provider="",
        platform="",
        pass_session_id=False,
        session_id="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _captured_context_cwd(agent):
    """The cwd build_system_prompt_parts hands to build_context_files_prompt."""
    captured = {}

    def fake_context_files(cwd=None, skip_soul=False, context_length=None):
        captured["cwd"] = cwd
        return ""

    with (
        patch("run_agent.load_profile_system_md", return_value=""),
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_nous_subscription_prompt", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", side_effect=fake_context_files),
    ):
        build_system_prompt_parts(agent)
    return captured["cwd"]


class TestContextFileCwd:
    def test_none_when_terminal_cwd_unset(self, monkeypatch):
        # Unset → None, so discovery falls back to the launch dir inside
        # build_context_files_prompt (the local-CLI #19242 contract).
        monkeypatch.delenv("TERMINAL_CWD", raising=False)
        assert _captured_context_cwd(_make_agent()) is None

    def test_configured_dir_when_terminal_cwd_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        assert _captured_context_cwd(_make_agent()) == tmp_path


def _stable_prompt(agent):
    with (
        patch("run_agent.load_profile_system_md", return_value=""),
        patch("run_agent.load_soul_md", return_value=""),
        patch("run_agent.build_nous_subscription_prompt", return_value=""),
        patch("run_agent.build_environment_hints", return_value=""),
        patch("run_agent.build_context_files_prompt", return_value=""),
    ):
        return build_system_prompt_parts(agent)["stable"]


def _init_code_repo(path):
    """A git repo that actually holds code — the coding posture requires a source
    file (or manifest), not a bare ``.git`` (a prose/notes repo stays general)."""
    import subprocess

    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    (path / "main.py").write_text("print('hi')\n")


class TestCodingContextBlock:
    def test_injected_when_active(self, monkeypatch, tmp_path):
        _init_code_repo(tmp_path)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        stable = _stable_prompt(agent)
        assert "coding agent" in stable
        assert "Workspace" in stable

    def test_absent_when_off(self, monkeypatch, tmp_path):
        _init_code_repo(tmp_path)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=["read_file"], platform="cli")
        # Drive the real path: force the resolved mode to "off" via config.
        with patch("agent.coding_context._coding_mode", return_value="off"):
            stable = _stable_prompt(agent)
        assert "coding agent" not in stable

    def test_absent_without_tools(self, monkeypatch, tmp_path):
        _init_code_repo(tmp_path)
        monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
        agent = _make_agent(valid_tool_names=[], platform="cli")
        assert "coding agent" not in _stable_prompt(agent)


class TestProfileCompositePromptMode:
    def test_composite_prompt_short_circuits_legacy_injections(self):
        agent = _make_agent(
            valid_tool_names=[],
            model="gpt-test",
            provider="test-provider",
            session_id="sess",
            pass_session_id=True,
        )

        with (
            patch("run_agent.load_profile_system_md", return_value="soul\n\nfront\n\nmem\n\nprojects"),
            patch("run_agent.load_soul_md", return_value="legacy soul"),
            patch("run_agent.build_nous_subscription_prompt", return_value="nous block"),
            patch("run_agent.build_environment_hints", return_value="env block"),
            patch("run_agent.build_context_files_prompt", return_value="project context"),
        ):
            parts = build_system_prompt_parts(agent, system_message="caller system")

        assert parts["stable"] == "soul\n\nfront\n\nmem\n\nprojects"
        assert parts["context"] == "caller system"
        assert parts["volatile"] == ""
        assert "legacy soul" not in parts["stable"]
        assert "Conversation started:" not in parts["volatile"]

    def test_composite_prompt_does_not_append_skills_prompt(self):
        agent = _make_agent(
            valid_tool_names=["skills_list", "skill_view"],
            _parallel_tool_call_guidance=False,
        )

        with (
            patch("run_agent.load_profile_system_md", return_value="soul\n\nfront\n\nmem\n\nprojects"),
            patch("run_agent.get_toolset_for_tool", return_value="skills"),
            patch("run_agent.build_skills_system_prompt", return_value="skills prompt"),
        ):
            stable = build_system_prompt_parts(agent)["stable"]

        assert stable == "soul\n\nfront\n\nmem\n\nprojects"
        assert "skills prompt" not in stable
        assert "Hermes Agent" not in stable
