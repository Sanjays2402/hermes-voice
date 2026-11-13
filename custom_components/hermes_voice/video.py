"""Video mode for the Hermes screen: the user's live picture in a corner while they talk to Hermes.

The picture itself is drawn on the Echo by the Hermes Eye app (docs/eye.md); this module decides when video
mode is on. It turns on when the user asks ("video mode") or when Hermes looks through the camera ("what am
I holding?"). It turns off when asked, after VIDEO_IDLE without a turn, or when the Echo reports the shutter
closed or the camera cut off. After NUDGE replies in a row that did not use the camera, the screen asks
whether the user is done with it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import time

VIDEO_IDLE = 300.0  # seconds without a turn before video mode ends by itself
NUDGE = 3  # replies in a row without the camera before the screen asks "done with the camera?"
LOOK_TOOL = "look"  # Hermes Eye's tool (extras/eye), called mcp__<server>__look by Hermes

_FILLER = re.compile(
    r"^(?:ok(?:ay)?|alright|all right|hey|hermes|please|and|so|now|um|uh|yes|yeah|no)\s+"
)
_CAMERA = r"(?:the )?(?:camera|video)"
COMMANDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "on",
        re.compile(
            rf"^(?:start |turn on |open )?(?:the )?video(?: mode| chat| call)?(?: on)?|"
            rf"(?:start|turn on|open|switch on) {_CAMERA}|{_CAMERA} on|"
            r"(?:lets|let us|can we) (?:do a )?video(?: chat| call)?$"
        ),
    ),
    (
        "off",
        re.compile(
            rf"^(?:stop|end|close|exit|quit|turn off|switch off|hang up)(?: the)? (?:video(?: mode| chat| call)?|camera)|"
            rf"{_CAMERA} off|(?:were |i am |im )?done with {_CAMERA}|no more video|hang up$"
        ),
    ),
    (
        "keep",
        re.compile(rf"^keep {_CAMERA}(?: on)?|(?:leave|keep) (?:it|video) on|im not done|not done yet$"),
    ),
)

SAY = {
    "on": "Video's on.",
    "off": "Camera's off.",
    "already_off": "The camera is already off.",
    "keep": "Okay, keeping the camera on.",
}


def parse_command(text: str) -> str | None:
    """The video command the whole utterance is (on, off, keep), or None: it goes to Hermes."""
    t = re.sub("['\u2019]", "", text.lower())
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+(?:please|now)$", "", t).strip()
    # Try the words as said, then drop one leading discourse word at a time. The filler strip
    # used to eat a whole run at once, so a command starting with a filler word ("no more video")
    # could never match: "no" was stripped before the off pattern ever saw it.
    while True:
        for action, pattern in COMMANDS:
            if pattern.fullmatch(t):
                return action
        stripped = _FILLER.sub("", t)
        if stripped == t:
            return None
        t = stripped


def is_look(tool: str) -> bool:
    return tool.rsplit("__", 1)[-1] == LOOK_TOOL


@dataclass(slots=True)
class VideoMode:
    """Video mode on one device."""

    since: float = field(default_factory=time.monotonic)
    last: float = field(default_factory=time.monotonic)  # the latest turn
    misses: int = 0  # replies in a row that did not use the camera
    asked: bool = False  # the screen has asked "done with the camera?"

    def as_dict(self) -> dict[str, object]:
        return {"on": True, "asked": self.asked}

    @property
    def idle(self) -> bool:
        return time.monotonic() - self.last > VIDEO_IDLE

    def turn_done(self, looked: bool) -> bool:
        """Count a Hermes reply. True when the screen should now ask whether the user is done."""
        self.last = time.monotonic()
        self.misses = 0 if looked else self.misses + 1
        if self.misses >= NUDGE and not self.asked:
            self.asked = True
            return True
        return False

    def keep(self) -> None:
        self.last = time.monotonic()
        self.misses = 0
        self.asked = False


CONTEXT = (
    "Video mode is on: the user is on camera at their Echo Show, and their live picture is on its screen. "
    "Use the look tool whenever their question might involve what they show you, where they are, or how "
    "something looks. Questions that have nothing to do with sight need no look."
)
