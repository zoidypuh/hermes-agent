"""Request-scoped Goblin clarification callback; ordinary CLI prompts stay local."""
import json
import re
import time
import uuid
from urllib.request import Request, urlopen


def bind_request(agent, message, *, turn_id=None):
    vars(agent).pop("_goblin_request", None)
    if getattr(agent, "platform", "") == "subagent" or getattr(agent, "_parent_session_id", None):
        return
    if not isinstance(message, str):
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


def ask(agent, route, questions):
    from tools.clarify_gateway import get_clarify_timeout

    notice = ("No user answer was received through Goblin Relay. Do not treat a recommended "
              "choice as accepted. Stop the dependent work and report the unanswered question.")
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
