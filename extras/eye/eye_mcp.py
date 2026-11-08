#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2"]
# ///
"""Hermes Eye for a Hermes voice profile: one MCP tool (stdio), `look`.

`look` takes a fresh picture with the Echo Show's camera (the Hermes Eye app, docs/eye.md) and returns it
as an image, so a model with vision sees it in the same turn. When the shutter is closed or the mute button
has cut the camera, it says so in words instead.

Environment:
  EYE_URL    e.g. http://<echo-ip>:28940
  EYE_TOKEN  the token given to the Hermes Eye app
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP, Image

URL = os.environ.get("EYE_URL", "").rstrip("/")
TOKEN = os.environ.get("EYE_TOKEN", "")

mcp = FastMCP("eye")


def snapshot() -> tuple[bytes | None, str]:
    """(JPEG, note) or (None, why not)."""
    if not URL or not TOKEN:
        return None, "Error: EYE_URL and EYE_TOKEN are not set."
    req = urllib.request.Request(URL + "/snapshot.jpg?q=80", headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read(), ""
    except urllib.error.HTTPError as err:
        if err.code == 423:
            try:
                status = json.loads(err.read() or b"{}")
            except ValueError:
                status = {}
            if status.get("covered"):
                return None, "The camera's shutter is closed, so it sees nothing. Ask the user to open it."
            if status.get("state") in ("blocked", "muted"):
                return None, "The camera is off: the Echo's mute button is on (it cuts the camera and microphones)."
            return None, "The camera gave no picture just now. Try again."
        if err.code == 401:
            return None, "Error: the Echo camera rejected the token."
        return None, f"Error: the Echo camera answered {err.code}."
    except (urllib.error.URLError, TimeoutError):
        return None, "Error: the Echo camera is not reachable."


@mcp.tool()
def look(prompt: str = "") -> list:
    """See through the camera on the user's Echo Show (the screen they are talking to). Takes a fresh
    photo and returns it. Use it only when the user asks you to look: what you see, what they are holding,
    who is there, read this. prompt: what to look for, in a few words."""
    jpg, why = snapshot()
    if jpg is None:
        return [why]
    return ["A photo from the Echo Show's camera, taken just now. The user is likely facing it.",
            Image(data=jpg, format="jpeg")]


if __name__ == "__main__":
    mcp.run()
