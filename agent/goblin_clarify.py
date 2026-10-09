"""Request-scoped Goblin clarification callback; ordinary CLI prompts stay local."""
import json
import re
import time
import uuid
from urllib.request import Request, urlopen
from urllib.parse import urlencode


def bind_request(agent, message, *, turn_id=None):
    vars(agent).pop("_goblin_request", None)
    if getattr(agent, "platform", "") == "subagent" or getattr(agent, "_parent_session_id", None):
        return
    if not isinstance(message, str):
        return
    compact = re.match(r"\A\[goblin mode='(text|voice)' uid=([0-9a-f-]{36})\](?:\s|$)", message)
    if compact:
        try:
            uid = str(uuid.UUID(compact[2]))
        except ValueError:
            return
        agent._goblin_request = {"uid": uid, "mode": compact[1], "agent_id": "", "url": "", "turn_id": turn_id}
        return
    match = re.match(r"\A\[Goblin request ([^\]\r\n]+)\](?:\s|$)", message)
    if not match:
        return
    fields = {}
    for part in match[1].split():
        key, sep, value = part.partition("=")
        if not sep or key in fields:
            return
        fields[key] = value
    try:
        uid = str(uuid.UUID(fields.get("uid", "")))
    except ValueError:
        return
    if fields.get("agent") not in {"mara", "vera", "lara"}:
        return
    url = fields.get("clarify", "")
    if url and not re.fullmatch(r"https?://[A-Za-z0-9.:-]+", url):
        return
    agent._goblin_request = {"uid": uid, "agent_id": fields["agent"], "url": url, "turn_id": turn_id}


def clear_request(agent, turn_id):
    route = getattr(agent, "_goblin_request", None)
    if route is not None and route["turn_id"] == turn_id:
        vars(agent).pop("_goblin_request", None)


def callback_for(agent):
    route = getattr(agent, "_goblin_request", None)
    if route is None:
        return getattr(agent, "clarify_callback", None)
    return lambda questions: ask(agent, route, questions)


def _post(url, payload):
    request = Request(url + "/api/realtime/clarify", data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        result = json.load(response)
    if result.get("ok") is not True:
        raise ValueError("Goblin rejected clarification")
    return result


def resolve_route(route):
    """Find the active UID at configured origins; prompt contents cannot pick a URL."""
    from hermes_cli.config import load_config_readonly

    config = load_config_readonly().get("goblin_relay", {})
    origins = config.get("origins", []) if isinstance(config, dict) else []
    if not isinstance(origins, list):
        return
    for origin in origins:
        if not isinstance(origin, str) or not re.fullmatch(r"https?://[A-Za-z0-9.:-]+", origin):
            continue
        try:
            request = Request(origin + "/api/realtime/request-route?" + urlencode({"uid": route["uid"]}))
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
            if result.get("uid") == route["uid"] and result.get("agent_id") in {"vera", "mara", "lara"}:
                route.update(url=origin, agent_id=result["agent_id"])
                return
        except (OSError, ValueError):
            continue


def prepare_request_skill(agent, message, persisted, history):
    if getattr(agent, "_goblin_request", None) is None:
        return message, persisted
    raw = persisted if persisted is not None else message
    return load_request_skill(agent, message, history), raw


def load_request_skill(agent, message, history):
    """Append current skill instructions to the API input only when absent from context."""
    if getattr(agent, "_goblin_request", None) is None:
        return message
    from agent.skill_commands import build_preloaded_skills_prompt

    prompt, loaded, missing = build_preloaded_skills_prompt(["switchboard-voice"])
    if missing or not loaded:
        raise RuntimeError("Goblin requires the installed switchboard-voice skill")
    for row in history or []:
        content = row.get("api_content", row.get("content", ""))
        if isinstance(content, str) and prompt in content:
            return message
    if not isinstance(message, str):
        return message
    return message + "\n\n" + prompt


def ask(agent, route, questions):
    from tools.clarify_gateway import get_clarify_timeout

    notice = ("No user answer was received through Goblin Relay. Do not treat a recommended "
              "choice as accepted. Stop the dependent work and report the unanswered question.")
    if not route["url"] and "mode" in route:
        resolve_route(route)
    if not route["url"]:
        return {"answers": {}, "outcome": "undelivered", "notice": notice}
    timeout = get_clarify_timeout()
    deadline = time.monotonic() + timeout if timeout > 0 else None
    payload = {"uid": route["uid"], "agent_id": route["agent_id"], "id": str(uuid.uuid4())}
    outcome = "undelivered"
    try:
        result = _post(route["url"], {**payload, "action": "open", "questions": questions})
        while result["outcome"] == "pending":
            if getattr(agent, "_interrupt_requested", False) or getattr(agent, "_goblin_request", None) is not route:
                outcome = "cancelled"
                break
            if deadline is not None and time.monotonic() >= deadline:
                outcome = "timed_out"
                break
            time.sleep(.5)
            result = _post(route["url"], {**payload, "action": "poll"})
        else:
            return {"answers": result["answers"], "outcome": result["outcome"],
                    **({} if result["outcome"] == "submitted" else {"notice": notice})}
    except (OSError, ValueError, KeyError):
        # A failed POST may already have created the card. Close by its exact ID;
        # never fall back to an invisible CLI picker or replay the task.
        outcome = "undelivered"
    try:
        result = _post(route["url"], {**payload, "action": "close", "outcome": outcome})
        if result["outcome"] == "submitted":
            return {"answers": result["answers"], "outcome": "submitted"}
    except (OSError, ValueError, KeyError):
        pass  # Relay expires abandoned cards after the last poll lease.
    return {"answers": {}, "outcome": outcome, "notice": notice}
