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

from . import tools

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


@mcp.tool(description=tools.DESCRIPTIONS["visual_memory"])
def visual_memory(action: str = "status", area: str = "") -> str:
    return tools.visual_memory(call, action, area)


@mcp.tool(description=tools.DESCRIPTIONS["what_changed"])
def what_changed(show: bool = True) -> str:
    return tools.what_changed(call, show)


@mcp.tool(description=tools.DESCRIPTIONS["find_object"])
def find_object(query: str, show: bool = False) -> str:
    return tools.find_object(call, query, show)


@mcp.tool(description=tools.DESCRIPTIONS["describe_scene"])
def describe_scene(which: str = "now") -> str:
    return tools.describe_scene(call, which)


@mcp.tool(description=tools.DESCRIPTIONS["object_history"])
def object_history(query: str = "", since: str = "", until: str = "") -> str:
    return tools.object_history(call, query, since, until)


@mcp.tool(description=tools.DESCRIPTIONS["show_evidence"])
def show_evidence(what: str = "comparison", query: str = "") -> str:
    return tools.show_evidence(call, what, query)


@mcp.tool(description=tools.DESCRIPTIONS["forget_visual_memory"])
def forget_visual_memory(scope: str, older_than_days: float = 0) -> str:
    return tools.forget_visual_memory(call, scope, older_than_days)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
