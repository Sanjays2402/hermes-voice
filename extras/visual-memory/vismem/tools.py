"""The visual memory tools the agent sees, shared by the MCP server and the Hermes plugin.

Each tool takes `call(path, params=None, body=None, timeout=30) -> dict`, which asks the visual-memory API,
and returns text written to be spoken.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Call = Callable[..., dict]

KEEP = (
    "Speak from the answer. Keep its distinctions: 'may be obstructed' is not 'gone', and not seeing "
    "something is not proof it was taken away. Positions are as the user sees them, facing the Echo."
)

DESCRIPTIONS = {
    "visual_memory": (
        "Turn visual memory on or off, or check it. While on, the Echo Show's camera takes a picture every "
        "second (its red REC badge shows); it looks at the view once when turned on, keeps changed views on "
        'this computer, and looks again only when asked. action: "on", "off" (pause at once) or "status". '
        'area: "desk" (default: the desk or table in view; people, chairs and the room behind are ignored) '
        'or "all" (the whole view, e.g. "watch the whole room", "watch the door").'
    ),
    "what_changed": (
        "What changed in front of the Echo Show's camera since it last looked: compares that view with the "
        'current one, taking a quick fresh look if needed. Use for "what changed?", "what\'s different?", '
        '"what did I take away?", "what\'s missing?", "did anything move?". show: put the before/after '
        "pictures, with the details, on the Echo's screen. Say ONLY the short version it gives you, in a "
        "sentence or two: the details are on the screen."
    ),
    "find_object": (
        "Where an object was last seen by the Echo Show's camera, from visual memory: \"where's my red "
        'screwdriver?", "do you still see my keys?", "what was next to the Raspberry Pi?". query: the '
        "thing, with any colour or detail the user said. show: put the picture where it was last seen on "
        'the Echo\'s screen (use when they say "show me"). ' + KEEP
    ),
    "describe_scene": (
        'What the Echo Show\'s camera sees or saw. Use which="now" for "what\'s in front of you?", "what '
        'do you see?", "what\'s on my desk?" (it takes a quick fresh look if the view changed). Also which='
        '"before_obstruction" (the last view before the camera was obstructed), or a time: "earlier", '
        '"today", "yesterday", "3h" (hours ago), or an ISO date-time. ' + KEEP
    ),
    "object_history": (
        'Whether and when something was seen in a time range: "was there a black box here yesterday?", '
        '"what was on the desk this morning?". query: the thing (empty for everything). since/until: '
        '"today", "yesterday", "earlier", "3h", "2d" or an ISO date-time (default: the last week). ' + KEEP
    ),
    "show_evidence": (
        'Put a stored picture on the Echo\'s screen: what="comparison" (the latest before/after, changes '
        'boxed), "item" (where `query` was last seen, boxed) or "scene" (the last reliable view). Pictures '
        "are the camera's own snapshots; nothing is drawn but boxes and labels."
    ),
    "forget_visual_memory": (
        'Delete visual memory when the user asks. scope: "all" (every observation and picture), "images" '
        '(pictures only), "session" (this session), or "older" (with older_than_days). This can\'t be '
        "undone: use it only when the user clearly asked."
    ),
}

_S = {"type": "string"}
_B = {"type": "boolean"}

PARAMETERS: dict[str, dict[str, Any]] = {
    "visual_memory": {
        "action": {**_S, "enum": ["on", "off", "status"], "default": "status"},
        "area": {**_S, "enum": ["", "desk", "all"], "default": ""},
    },
    "what_changed": {"show": {**_B, "default": True}},
    "find_object": {"query": _S, "show": {**_B, "default": False}},
    "describe_scene": {"which": {**_S, "default": "now"}},
    "object_history": {
        "query": {**_S, "default": ""},
        "since": {**_S, "default": ""},
        "until": {**_S, "default": ""},
    },
    "show_evidence": {
        "what": {**_S, "enum": ["comparison", "item", "scene"], "default": "comparison"},
        "query": {**_S, "default": ""},
    },
    "forget_visual_memory": {
        "scope": {**_S, "enum": ["all", "images", "session", "older"]},
        "older_than_days": {"type": "number", "default": 0},
    },
}
REQUIRED = {"find_object": ["query"], "forget_visual_memory": ["scope"]}


def schema(name: str) -> dict[str, Any]:
    """The OpenAI-style function schema for one tool."""
    return {
        "name": name,
        "description": DESCRIPTIONS[name],
        "parameters": {"type": "object", "properties": PARAMETERS[name], "required": REQUIRED.get(name, [])},
    }


def _brief(r: dict, screen: bool) -> str:
    short, full = r.get("brief"), r.get("say", "No answer from visual memory.")
    if not short:
        return full
    where = "; they are on the screen" if screen else ""
    return f"Say just this: {short}\n(Details, for follow-up questions{where}: {full})"


def visual_memory(call: Call, action: str = "status", area: str = "") -> str:
    if action in ("on", "off"):
        body = {"on": action == "on", "area": area if area in ("desk", "all") else None}
        r = call("/session", body=body)
        if "on" in r:
            if not r["on"]:
                return "Visual memory is off: I've stopped watching. What I remembered is kept."
            where = "the whole view" if r.get("area") == "all" else "your desk"
            return f"Say just this: OK, I'm watching {where}. (Done: no status check is needed.)"
        return r.get("say", "Couldn't change visual memory.")
    return call("/status").get("say", "Visual memory status is unavailable.")


def what_changed(call: Call, show: bool = True) -> str:
    return _brief(call("/changed", {"fresh": "1", "show": "1" if show else "0"}, timeout=40), screen=True)


def find_object(call: Call, query: str, show: bool = False) -> str:
    r = call("/find", {"q": query}, timeout=40)
    if show and r.get("items"):
        s = call("/show", body={"what": "item", "query": query})
        if s.get("say"):
            return r.get("say", "") + " " + s["say"]
    return r.get("say", "No answer from visual memory.")


def describe_scene(call: Call, which: str = "now") -> str:
    return _brief(call("/scene", {"which": which}, timeout=40), screen=False)


def object_history(call: Call, query: str = "", since: str = "", until: str = "") -> str:
    return call("/history", {"q": query, "since": since, "until": until}).get("say", "No answer.")


def show_evidence(call: Call, what: str = "comparison", query: str = "") -> str:
    r = call("/show", body={"what": what, "query": query})
    return r.get("say") or ("It's on the screen." if r.get("shown") else "Couldn't show that.")


def forget_visual_memory(call: Call, scope: str, older_than_days: float = 0) -> str:
    body: dict = {"scope": scope}
    if scope == "older":
        body["days"] = older_than_days
    return call("/forget", body=body).get("say", "Couldn't delete.")


HANDLERS: dict[str, Callable[..., str]] = {
    "visual_memory": visual_memory,
    "what_changed": what_changed,
    "find_object": find_object,
    "describe_scene": describe_scene,
    "object_history": object_history,
    "show_evidence": show_evidence,
    "forget_visual_memory": forget_visual_memory,
}
