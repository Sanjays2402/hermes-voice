"""Visual memory tools for Hermes (MCP, stdio). Each tool asks the local visual-memory service.

Environment:
  VISMEM_URL         default http://127.0.0.1:28950
  VISMEM_TOKEN_FILE  default ~/.local/share/hermes-visual-memory/api_token
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

URL = os.environ.get("VISMEM_URL", "http://127.0.0.1:28950").rstrip("/")
TOKEN_FILE = Path(
    os.environ.get("VISMEM_TOKEN_FILE", "~/.local/share/hermes-visual-memory/api_token")
).expanduser()

mcp = FastMCP("vismem")


def call(path: str, params: dict | None = None, body: dict | None = None, timeout: float = 30) -> dict:
    url = URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        token = TOKEN_FILE.read_text().strip()
    except OSError:
        return {"say": "Visual memory isn't set up on this computer."}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"say": "The visual memory service isn't running."}


@mcp.tool()
def visual_memory(action: str = "status", area: str = "") -> str:
    """Turn visual memory on or off, or check it. While on, the Echo Show's camera takes a picture every
    second (its red REC badge shows); it looks at the view once when turned on, keeps changed views on this
    computer, and looks again only when asked. action: "on", "off" (pause at once) or "status".
    area: "desk" (default: the desk or table in view; people, chairs and the room behind are ignored) or
    "all" (the whole view, e.g. "watch the whole room", "watch the door")."""
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


@mcp.tool()
def what_changed(show: bool = True) -> str:
    """What changed in front of the Echo Show's camera since it last looked: compares that view with the
    current one, taking a quick fresh look if needed. Use for "what changed?", "what's different?", "what
    did I take away?", "what's missing?", "did anything move?". show: put the
    before/after pictures, with the details, on the Echo's screen.
    Say ONLY the short version it gives you, in a sentence or two: the details are on the screen."""
    r = call("/changed", {"fresh": "1", "show": "1" if show else "0"}, timeout=40)
    short, full = r.get("brief"), r.get("say", "No answer from visual memory.")
    if not short:
        return full
    return f"Say just this: {short}\n(Details, for follow-up questions; they are on the screen: {full})"


@mcp.tool()
def find_object(query: str, show: bool = False) -> str:
    """Where an object was last seen by the Echo Show's camera, from visual memory: "where's my red
    screwdriver?", "do you still see my keys?", "what was next to the Raspberry Pi?". query: the thing, with
    any colour or detail the user said. show: put the picture where it was last seen on the Echo's screen
    (use when they say "show me"). Speak from the answer. Keep its distinctions: 'may be obstructed' is not 'gone', and not seeing
    something is not proof it was taken away. Positions are as the user sees them, facing the Echo."""
    r = call("/find", {"q": query}, timeout=40)
    if show and r.get("items"):
        s = call("/show", body={"what": "item", "query": query})
        if s.get("say"):
            return r.get("say", "") + " " + s["say"]
    return r.get("say", "No answer from visual memory.")


@mcp.tool()
def describe_scene(which: str = "now") -> str:
    """What the Echo Show's camera sees or saw. Use which="now" for "what's in front of you?", "what do you
    see?", "what's on my desk?" (it takes a quick fresh look if the view changed). Also which=
    "before_obstruction" (the last view before the camera was obstructed), or a time: "earlier", "today",
    "yesterday", "3h" (hours ago), or an ISO date-time. Speak from the answer. Keep its distinctions: 'may be obstructed' is not 'gone', and not seeing
    something is not proof it was taken away. Positions are as the user sees them, facing the Echo."""
    r = call("/scene", {"which": which}, timeout=40)
    short, full = r.get("brief"), r.get("say", "No answer from visual memory.")
    if not short:
        return full
    return f"Say just this: {short}\n(Details, for follow-up questions: {full})"


@mcp.tool()
def object_history(query: str = "", since: str = "", until: str = "") -> str:
    """Whether and when something was seen in a time range: "was there a black box here yesterday?", "what
    was on the desk this morning?". query: the thing (empty for everything). since/until: "today",
    "yesterday", "earlier", "3h", "2d" or an ISO date-time (default: the last week). Speak from the answer. Keep its distinctions: 'may be obstructed' is not 'gone', and not seeing
    something is not proof it was taken away. Positions are as the user sees them, facing the Echo."""
    return call("/history", {"q": query, "since": since, "until": until}).get("say", "No answer.")


@mcp.tool()
def show_evidence(what: str = "comparison", query: str = "") -> str:
    """Put a stored picture on the Echo's screen: what="comparison" (the latest before/after, changes
    boxed), "item" (where `query` was last seen, boxed) or "scene" (the last reliable view). Pictures are the
    camera's own snapshots; nothing is drawn but boxes and labels."""
    r = call("/show", body={"what": what, "query": query})
    return r.get("say") or ("It's on the screen." if r.get("shown") else "Couldn't show that.")


@mcp.tool()
def forget_visual_memory(scope: str, older_than_days: float = 0) -> str:
    """Delete visual memory when the user asks. scope: "all" (every observation and picture), "images"
    (pictures only), "session" (this session), or "older" (with older_than_days). This can't be undone:
    use it only when the user clearly asked."""
    body: dict = {"scope": scope}
    if scope == "older":
        body["days"] = older_than_days
    return call("/forget", body=body).get("say", "Couldn't delete.")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
