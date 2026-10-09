"""Compact Goblin input loads the installed skill without repeating it over tmux."""
from types import SimpleNamespace

import pytest

from agent import goblin_clarify as goblin

UID = "807c6dde-253f-4754-92f5-419b89319a6c"


def test_compact_modes_and_literal_task_load_real_skill_once(tmp_path, monkeypatch):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from agent.skill_commands import reload_skills

    home = tmp_path / "hermes"
    skill = home / "skills" / "switchboard-voice" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: switchboard-voice\ndescription: Goblin reply rules\n---\nRule: text is silent; voice uses current UID.\n")
    (home / "config.yaml").write_text("skills:\n  disabled: []\n")
    monkeypatch.chdir(tmp_path)
    token = set_hermes_home_override(str(home))
    reload_skills()
    try:
        agent = SimpleNamespace(platform="cli")
        raw = f"[goblin mode='text' uid={UID}] Quote \"hi\".\n\tKeep $HOME and `literal` 😊"
        goblin.bind_request(agent, raw)
        assert agent._goblin_request["mode"] == "text"
        api = goblin.load_request_skill(agent, raw, [])
        assert api.startswith(raw + "\n\n")
        assert "Rule: text is silent" in api
        history = [{"role":"user", "content":raw, "api_content":api}]
        voice = f"[goblin mode='voice' uid={UID}] Next task"
        goblin.bind_request(agent, voice)
        assert agent._goblin_request["mode"] == "voice"
        assert goblin.load_request_skill(agent, voice, history) == voice
        assert "Rule: text is silent" in goblin.load_request_skill(agent, voice, [])
        skill.write_text(skill.read_text() + "Updated rule.\n")
        assert "Updated rule." in goblin.load_request_skill(agent, voice, history)
        skill.unlink()
        with pytest.raises(RuntimeError, match="installed switchboard-voice"):
            goblin.load_request_skill(agent, voice, [])
    finally:
        reset_hermes_home_override(token)
        reload_skills()


def test_invalid_headers_ordinary_cli_and_subagents_do_not_load_skill():
    agent = SimpleNamespace(platform="cli")
    for raw in ("ordinary", f"[goblin mode='other' uid={UID}] task", "[goblin mode='text' uid=invalid] task",
                f"[goblin mode='text' uid={UID} clarify=http://attacker] task"):
        goblin.bind_request(agent, raw)
        assert not hasattr(agent, "_goblin_request")
        assert goblin.load_request_skill(agent, raw, []) == raw
    agent.platform = "subagent"
    raw = f"[goblin mode='text' uid={UID}] task"
    goblin.bind_request(agent, raw)
    assert goblin.load_request_skill(agent, raw, []) == raw
