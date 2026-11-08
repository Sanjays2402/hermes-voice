"""Live feed for a Hermes screen on an Echo Show or a browser (docs/show-screen.md).

The screen page gets everything through two Home Assistant websocket commands, so it needs only a
Home Assistant token (any user, not admin) and never the Hermes API key:

- ``hermes_voice/screen/subscribe``: the voice device's state (idle, listening, processing, responding),
  what was heard, Hermes' reply as it is written, its tools, and the answers Home Assistant gave itself.
- ``hermes_voice/screen/overview``: Hermes' scheduled jobs, recent sessions and gateway status.
- ``hermes_voice/screen/retry``: tap to correct. Stops the turn on an Echo and listens again, without the
  wake word; Hermes is told what it misheard.
- ``hermes_voice/screen/cook``, ``hermes_voice/screen/recipes``, ``hermes_voice/screen/start_timer``: recipes
  from Mealie (recipes.py): open one, move through its steps, tick off ingredients, browse, start a step's timer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
from pathlib import Path
import time
from typing import Any, ClassVar
from urllib.parse import urlsplit

from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs
from homeassistant.components.http import KEY_HASS, HomeAssistantView, StaticPathConfig
from homeassistant.components.intent.const import TIMER_DATA
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, intent
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import recipes, video
from .client import HermesError
from .const import (
    ACTIVITY_OFF,
    CONF_ACTIVITY_PORT,
    CONF_SCREEN_ACTIVITY,
    DEFAULT_ACTIVITY_PORT,
    DEFAULT_SCREEN_ACTIVITY,
    DOMAIN,
    LOGGER,
)

DATA_SCREEN = f"{DOMAIN}_screen"
SATELLITE = "assist_satellite"
SCREEN_URL = "/hermes_voice/screen"
SCREEN_DIR = Path(__file__).parent / "screen"
OVERVIEW_TTL = 25.0  # the screen asks about twice a minute while idle
MAX_TEXT = 4000
RETRY_WINDOW = 120.0  # seconds after a tap in which the next request continues the same Hermes session
RETRY_MESSAGE = "Go ahead."  # spoken when the Echo listens again after a tap
RETRY_PROMPT = (
    'The user tapped "misheard" on the screen: speech-to-text got their last request wrong ("{heard}"). '
    "What they say next is what they meant; ignore the misheard request."
)
VIDEO_CHECK = 30.0  # seconds between checks for video modes that have gone idle
RECIPE_SETTLE = 0.8  # seconds to wait for more recipe lookups in a turn before showing what Hermes opened
# The page's files, hashed once at start: a screen that reconnects after an update reloads itself.
PAGE_FILES = (
    "index.html",
    "app.js",
    "style.css",
)  # characters of one reply kept for a screen that connects late

type ScreenCallback = Callable[[dict[str, Any]], None]

# Fields passed on from Hermes; everything else (prompts, models, scripts, costs) stays in Home Assistant.
SESSION_FIELDS = (
    "id",
    "title",
    "preview",
    "last_active",
    "started_at",
    "message_count",
    "tool_call_count",
    "source",
)
JOB_FIELDS = (
    "id",
    "name",
    "schedule_display",
    "next_run_at",
    "last_run_at",
    "last_status",
    "enabled",
    "state",
)
HEALTH_FIELDS = ("status", "gateway_busy", "active_agents", "metrics_today", "version")
TASK_FIELDS = ("id", "title", "assignee", "status", "board", "started_at", "completed_at")


@dataclass(slots=True)
class Turn:
    """The latest exchange on one device, for screens that connect in the middle of it."""

    heard: str = ""
    reply: str = ""
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    phase: str = "idle"  # thinking, answering, done, error
    by: str = "hermes"  # or "home_assistant"
    at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "heard": self.heard,
            "reply": self.reply,
            "tools": list(self.tools.values()),
            "phase": self.phase,
            "by": self.by,
            "at": self.at,
        }


class ScreenHub:
    """Fans turn events out to the screens that watch a device (or every device)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._subscribers: list[tuple[str | None, ScreenCallback]] = []
        self.turns: dict[str | None, Turn] = {}
        self.agent_ids: set[str] = set()  # Hermes conversation entities: their turns are reported directly
        self._heard: dict[str, str] = {}  # conversation id -> the user's last words, until an answer comes
        self.sessions: dict[str | None, str] = {}  # device -> Hermes session of its latest turn
        self._retry: dict[str | None, tuple[str, float]] = {}  # device -> (session to continue, when tapped)
        self._overview: dict[str, tuple[float, dict[str, Any]]] = {}
        self.cooking: dict[str | None, recipes.Cooking] = {}  # device -> the recipe open on its screen
        self._recipe_refs: dict[str | None, list[str]] = {}  # device -> recipes Hermes read this turn
        self._recipe_timer: dict[str | None, Callable[[], None]] = {}
        self.video: dict[str | None, video.VideoMode] = {}  # device -> video mode, while it is on
        self._looked: set[str | None] = set()  # devices whose current turn used the camera
        self._video_check: Callable[[], None] | None = None
        self.page_version = ""
        self.page_html = ""

    @callback
    def async_subscribe(self, device_id: str | None, cb: ScreenCallback) -> Callable[[], None]:
        """Watch one device, or every device with None. Returns the unsubscribe function."""
        entry = (device_id, cb)
        self._subscribers.append(entry)

        @callback
        def unsubscribe() -> None:
            if entry in self._subscribers:
                self._subscribers.remove(entry)

        return unsubscribe

    @callback
    def publish(self, device_id: str | None, event: dict[str, Any]) -> None:
        """Send one event to the screens of that device and the ones watching all devices."""
        event = {**event, "device_id": device_id}
        for wanted, cb in list(self._subscribers):
            if wanted is None or wanted == device_id:
                try:
                    cb(event)
                except Exception:
                    LOGGER.exception("Hermes screen subscriber failed")

    # Events of a Hermes turn (called by the conversation entity).

    @callback
    def turn_start(self, device_id: str | None, heard: str) -> None:
        self.turns[device_id] = Turn(heard=heard, phase="thinking", at=time.time())
        self._recipe_refs[device_id] = []
        self._looked.discard(device_id)
        if (v := self.video.get(device_id)) is not None:
            v.last = time.monotonic()
        self.publish(device_id, {"type": "heard", "text": heard, "by": "hermes"})

    @callback
    def delta(self, device_id: str | None, text: str) -> None:
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        if len(turn.reply) < MAX_TEXT:
            turn.reply += text
        turn.phase = "answering"
        self.publish(device_id, {"type": "delta", "text": text})

    @callback
    def tool(self, device_id: str | None, tool_call_id: str, tool: str, label: str, running: bool) -> None:
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        if not running and (started := turn.tools.get(tool_call_id)):
            label = started["label"]  # Hermes' end event carries the tool's name, not the friendly label
        turn.tools[tool_call_id] = {"id": tool_call_id, "tool": tool, "label": label, "running": running}
        self.publish(
            device_id,
            {
                "type": "tool_start" if running else "tool_end",
                "id": tool_call_id,
                "tool": tool,
                "label": label,
            },
        )
        if running:
            self._recipe_tool(device_id, tool, label)
            if video.is_look(tool):
                self._looked.add(device_id)
                self.video_on(device_id, "look")

    @callback
    def reply_done(self, device_id: str | None, text: str) -> None:
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        turn.reply, turn.phase = text[:MAX_TEXT], "done"
        self.publish(device_id, {"type": "reply_done", "text": turn.reply})
        if (v := self.video.get(device_id)) is not None and v.turn_done(device_id in self._looked):
            self.publish(device_id, {"type": "video", "on": True, "ask": True})

    @callback
    def cancelled(self, device_id: str | None) -> str:
        """The user tapped to correct: the turn stops. Returns what was heard."""
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        turn.phase = "cancelled"
        if session := self.sessions.get(device_id):
            self._retry[device_id] = (session, time.monotonic())
        self.publish(device_id, {"type": "cancelled"})
        return turn.heard

    @callback
    def take_retry(self, device_id: str | None) -> str | None:
        """The session a corrected request continues: Home Assistant starts a new conversation for it."""
        session, at = self._retry.pop(device_id, (None, 0.0))
        return session if session and time.monotonic() - at < RETRY_WINDOW else None

    @callback
    def error(self, device_id: str | None, message: str) -> None:
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        turn.phase = "error"
        self.publish(device_id, {"type": "error", "message": message})

    # Recipes (recipes.py): what Hermes reads shows on the screen; cooking commands are answered here.

    @callback
    def _recipe_tool(self, device_id: str | None, tool: str, label: str) -> None:
        """Hermes used the Mealie MCP server (extras/mealie): show the recipe it read, or what it found.

        The label is the tool's main argument (Hermes' preview of it). Several recipes read in one turn
        ("which of these is quickest?") show as a list instead of one after the other.
        """
        name = tool.rsplit("__", 1)[-1]
        label = label.strip()
        if "mealie" not in tool.lower() or not label or recipes.mealie_entry(self.hass) is None:
            return
        if name == "search_recipes":
            self.hass.async_create_task(self.async_show_search(device_id, label))
        elif name == "get_recipe":
            refs = self._recipe_refs.setdefault(device_id, [])
            if label not in refs:
                refs.append(label)
            if cancel := self._recipe_timer.pop(device_id, None):
                cancel()

            async def show(_now: Any) -> None:
                self._recipe_timer.pop(device_id, None)
                await self._async_show_refs(device_id)

            self._recipe_timer[device_id] = async_call_later(self.hass, RECIPE_SETTLE, show)

    async def _async_show_refs(self, device_id: str | None) -> None:
        refs = list(self._recipe_refs.get(device_id) or [])
        if len(refs) == 1:
            await self.async_open_recipe(device_id, refs[0])
            return
        found = await asyncio.gather(
            *(recipes.async_search(self.hass, r, 1) for r in refs), return_exceptions=True
        )
        items = [hits[0] for hits in found if isinstance(hits, list) and hits]
        if items:
            self.publish(device_id, {"type": "recipes", "title": "Recipes Hermes looked at", "items": items})

    async def async_show_search(self, device_id: str | None, query: str) -> None:
        try:
            items = await recipes.async_search(self.hass, query, 12)
        except recipes.RecipeError as err:
            LOGGER.debug("Recipe search for the screen failed: %s", err)
            return
        self.publish(device_id, {"type": "recipes", "title": query, "items": items})

    async def async_open_recipe(self, device_id: str | None, ref: str, view: str = "card") -> dict[str, Any]:
        """Open a recipe on a device's screen. The same recipe again keeps its place (Hermes re-reading it)."""
        try:
            recipe = await recipes.async_get_recipe(self.hass, ref)
        except recipes.RecipeError as err:
            LOGGER.debug("Recipe %r for the screen: %s", ref, err)
            raise
        cook = self.get_cook(device_id)
        if cook is None or cook.recipe.get("slug") != recipe.get("slug"):
            cook = self.cooking[device_id] = recipes.Cooking(recipe=recipe, view=view)
        else:
            cook.recipe = recipe
        self.publish_cook(device_id)
        return cook.as_dict()

    @callback
    def get_cook(self, device_id: str | None) -> recipes.Cooking | None:
        cook = self.cooking.get(device_id)
        if cook is not None and cook.stale:
            del self.cooking[device_id]
            return None
        return cook

    @callback
    def publish_cook(self, device_id: str | None) -> None:
        cook = self.get_cook(device_id)
        self.publish(device_id, {"type": "cook", "cook": cook.as_dict() if cook else None})

    @callback
    def close_cook(self, device_id: str | None) -> None:
        self.cooking.pop(device_id, None)
        self.publish_cook(device_id)

    @callback
    def cook_command(self, device_id: str | None, text: str) -> str | None:
        """Answer "next step", "back", "step 4", "ingredients", "close the recipe" while a recipe is open on
        this device. None: not a cooking command (or nothing open), so the turn goes to Hermes."""
        cook = self.get_cook(device_id)
        parsed = recipes.parse_command(text) if cook else None
        if cook is None or parsed is None:
            return None
        said, closed = recipes.apply_command(cook, *parsed)
        if closed:
            self.cooking.pop(device_id, None)
        self.publish_cook(device_id)
        self.turns[device_id] = Turn(
            heard=text, reply=said, phase="done", by="home_assistant", at=time.time()
        )
        self.publish(
            device_id, {"type": "local_answer", "heard": text, "text": said[:MAX_TEXT], "cook": True}
        )
        return said

    @callback
    def cook_context(self, device_id: str | None) -> str | None:
        cook = self.get_cook(device_id)
        return cook.context() if cook else None

    # Video mode (video.py): the user's live picture on the screen while they talk to Hermes.

    @callback
    def video_on(self, device_id: str | None, why: str) -> None:
        v = self.video.get(device_id)
        if v is None:
            self.video[device_id] = video.VideoMode()
            self.publish(device_id, {"type": "video", "on": True, "why": why})
            if self._video_check is None:
                self._video_check = async_call_later(self.hass, VIDEO_CHECK, self._video_tick)
        else:
            v.keep()

    @callback
    def video_off(self, device_id: str | None, why: str) -> bool:
        if self.video.pop(device_id, None) is None:
            return False
        if not self.video and self._video_check is not None:
            self._video_check()  # nothing left to time out
            self._video_check = None
        self.publish(device_id, {"type": "video", "on": False, "why": why})
        return True

    @callback
    def video_keep(self, device_id: str | None) -> bool:
        if (v := self.video.get(device_id)) is None:
            return False
        v.keep()
        self.publish(device_id, {"type": "video", "on": True, "why": "keep"})
        return True

    @callback
    def _video_tick(self, _now: Any = None) -> None:
        self._video_check = None
        for device_id, v in list(self.video.items()):
            if v.idle:
                self.video_off(device_id, "timeout")
        if self.video:
            self._video_check = async_call_later(self.hass, VIDEO_CHECK, self._video_tick)

    @callback
    def video_command(self, device_id: str | None, text: str) -> str | None:
        """Answer "video mode", "stop video", "keep the camera on" at once. None: not a video command."""
        action = video.parse_command(text)
        if action is None:
            return None
        if action == "on":
            self.video_on(device_id, "voice")
            said = video.SAY["on"]
        elif action == "off":
            said = video.SAY["off"] if self.video_off(device_id, "voice") else video.SAY["already_off"]
        elif self.video_keep(device_id):
            said = video.SAY["keep"]
        else:
            return None  # "keep it on" with no video: something else, for Hermes
        self.turns[device_id] = Turn(
            heard=text, reply=said, phase="done", by="home_assistant", at=time.time()
        )
        self.publish(device_id, {"type": "local_answer", "heard": text, "text": said})
        return said

    @callback
    def video_context(self, device_id: str | None) -> str | None:
        return video.CONTEXT if device_id in self.video else None

    # Turns Home Assistant answered itself (Prefer handling commands locally).

    @callback
    def on_chat_log(self, conversation_id: str, event_type: str, data: dict[str, Any]) -> None:
        """HA reports each message as it is added (content_added); the user's comes first."""
        if event_type == "deleted":
            self._heard.pop(conversation_id, None)
            return
        if event_type != "content_added":
            return
        content = data.get("content") or {}
        if content.get("role") == "user":
            self._heard[conversation_id] = content.get("content") or ""
            return
        if content.get("role") != "assistant":
            return
        text = content.get("content") or ""
        if not text:
            # A message that only calls tools. With "Prefer handling commands locally" HA records its own
            # intent as a tool call under the pipeline's agent (Hermes) before its answer.
            return
        if content.get("agent_id") in self.agent_ids:
            self._heard.pop(conversation_id, None)
            return  # a Hermes answer: already reported, with its device
        heard = self._heard.pop(conversation_id, "")
        device_id = self._busy_device()
        self.turns[device_id] = Turn(
            heard=heard, reply=text[:MAX_TEXT], phase="done", by="home_assistant", at=time.time()
        )
        self.publish(device_id, {"type": "local_answer", "heard": heard, "text": text[:MAX_TEXT]})

    def _busy_device(self) -> str | None:
        """The device whose satellite is mid-turn: chat logs do not say which device they belong to."""
        registry = er.async_get(self.hass)
        busy = [
            entry.device_id
            for state in self.hass.states.async_all(SATELLITE)
            if state.state in ("processing", "responding")
            and (entry := registry.async_get(state.entity_id)) is not None
        ]
        return busy[0] if len(busy) == 1 else None

    # Overview from the Hermes gateway.

    async def async_overview(self, entry_id: str | None) -> dict[str, Any]:
        entries = [
            e
            for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
        ]
        if not entries:
            return {"error": "no loaded Hermes Voice entry"}
        entry = entries[0]
        cached = self._overview.get(entry.entry_id)
        if cached and time.monotonic() - cached[0] < OVERVIEW_TTL:
            return cached[1]
        client = entry.runtime_data.client
        paths = {"jobs": "/api/jobs", "sessions": "/api/sessions?limit=8", "health": "/health/detailed"}
        scope = entry.options.get(CONF_SCREEN_ACTIVITY, DEFAULT_SCREEN_ACTIVITY)
        if scope != ACTIVITY_OFF:
            # The read-only activity helper next to Hermes (screen_activity.py), same host as the gateway.
            host = urlsplit(client.base_url).hostname or "localhost"
            port = int(entry.options.get(CONF_ACTIVITY_PORT, DEFAULT_ACTIVITY_PORT))
            paths["activity"] = f"http://{host}:{port}/activity?scope={scope}"
        results = await asyncio.gather(
            # The helper answers from its cache; only its very first answer runs `hermes` (a few seconds).
            *(client.async_get_json(p, 15 if k == "activity" else 5) for k, p in paths.items()),
            return_exceptions=True,
        )
        out: dict[str, Any] = {"profile": entry.title, "errors": {}}
        for key, res in zip(paths, results, strict=True):
            if isinstance(res, HermesError):
                out["errors"][key] = str(res) or type(res).__name__
                continue
            if isinstance(res, BaseException):
                raise res
            if key == "jobs":
                out["jobs"] = [_pick(j, JOB_FIELDS) for j in res.get("jobs", []) if isinstance(j, dict)]
            elif key == "sessions":
                out["sessions"] = [
                    _pick(s, SESSION_FIELDS) for s in res.get("data", []) if isinstance(s, dict)
                ]
            elif key == "activity":
                out["activity"] = {
                    "scope": res.get("scope", scope),
                    **{
                        part: [_pick(t, TASK_FIELDS) for t in res.get(part, []) if isinstance(t, dict)]
                        for part in ("running", "attention", "recent")
                    },
                    "next_up": _next_up(res.get("next_up")),
                }
            else:
                out["health"] = _pick(res, HEALTH_FIELDS)
                # Which of Hermes' readiness checks are not ok, so a screen can say why ("disk 97%").
                checks = (res.get("readiness") or {}).get("checks") or {}
                out["health"]["warnings"] = [
                    {"check": name, "status": c.get("status"), "used_percent": c.get("used_percent")}
                    for name, c in checks.items()
                    if isinstance(c, dict) and c.get("status") not in (None, "ok")
                ]
        self._overview[entry.entry_id] = (time.monotonic(), out)
        return out


def _next_up(value: Any) -> dict[str, Any] | None:
    """The helper's opt-in suggestion line: its text (capped) and when it was written, nothing else."""
    if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value["text"].strip():
        return None
    at = value.get("at")
    return {"text": value["text"].strip()[:120], "at": at if isinstance(at, int | float) else None}


class ScreenPageView(HomeAssistantView):
    """The page itself, never cached, pointing at its files by version.

    The Echo Show's WebView kept an old ``app.js`` after an update: Home Assistant serves static files without
    cache headers, so browsers may reuse them. Here the page is marked ``no-store`` and loads
    ``app.js?v=<version>`` and ``style.css?v=<version>``, so new files always have new addresses.
    """

    url = SCREEN_URL
    extra_urls: ClassVar[list[str]] = [SCREEN_URL + "/", SCREEN_URL + "/index.html"]
    name = "hermes_voice:screen"
    requires_auth = False  # the page logs in to the websocket itself

    async def get(self, request: web.Request) -> web.Response:
        hub = async_get_hub(request.app[KEY_HASS])
        if hub is None or not hub.page_html:
            return web.Response(status=503, text="Hermes Voice is starting")
        return web.Response(
            text=hub.page_html,
            content_type="text/html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )


def _load_page() -> tuple[str, str]:
    """The page's version (a hash of its files) and its HTML with versioned, absolute file addresses."""
    digest = hashlib.sha256()
    for name in PAGE_FILES:
        path = SCREEN_DIR / name
        if path.is_file():
            digest.update(path.read_bytes())
    version = digest.hexdigest()[:12]
    index = SCREEN_DIR / "index.html"
    html = index.read_text(encoding="utf-8") if index.is_file() else ""
    for name in ("app.js", "style.css"):
        html = html.replace(f'"{name}"', f'"{SCREEN_URL}/{name}?v={version}"')
    return version, html


def _pick(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: data[k] for k in keys if k in data}


def satellite_entity(hass: HomeAssistant, device_id: str) -> str | None:
    """The assist_satellite entity of a device, if it has one."""
    registry = er.async_get(hass)
    return next(
        (e.entity_id for e in er.async_entries_for_device(registry, device_id) if e.domain == SATELLITE), None
    )


@callback
def async_get_hub(hass: HomeAssistant) -> ScreenHub | None:
    """The hub, once async_setup_screen ran."""
    return hass.data.get(DATA_SCREEN)


async def async_setup_screen(hass: HomeAssistant) -> None:
    """Create the hub, register the websocket commands and serve the screen page."""
    if DATA_SCREEN in hass.data:
        return
    hub = ScreenHub(hass)
    hub.page_version, hub.page_html = await hass.async_add_executor_job(_load_page)
    hass.data[DATA_SCREEN] = hub
    async_subscribe_chat_logs(hass, hub.on_chat_log)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_overview)
    websocket_api.async_register_command(hass, ws_stop_timer)
    websocket_api.async_register_command(hass, ws_retry)
    websocket_api.async_register_command(hass, ws_cook)
    websocket_api.async_register_command(hass, ws_recipes)
    websocket_api.async_register_command(hass, ws_start_timer)
    websocket_api.async_register_command(hass, ws_video)
    if SCREEN_DIR.is_dir() and getattr(hass, "http", None) is not None:
        hass.http.register_view(ScreenPageView())
        hass.http.register_view(recipes.RecipeImageView())
        await hass.http.async_register_static_paths([StaticPathConfig(SCREEN_URL, str(SCREEN_DIR), False)])


# What hassmic (echo-dot-assist) exposes for an Echo, by the entity's original name: mute and Do not disturb,
# a ringing timer and its names, and a button that stops it. Other voice devices simply lack them.
CONTROLS = {
    ("switch", "Mute"): "mute",
    ("switch", "Do not disturb"): "dnd",
    ("binary_sensor", "Timer ringing"): "ringing",
    ("sensor", "Ringing timers"): "ringing_names",
    ("button", "Stop ringing"): "stop",
}
TIMER_POLL = timedelta(seconds=2)


def device_controls(hass: HomeAssistant, device_id: str) -> dict[str, str]:
    """role -> entity_id for a device's controls (CONTROLS)."""
    out: dict[str, str] = {}
    for entry in er.async_entries_for_device(er.async_get(hass), device_id):
        role = CONTROLS.get((entry.domain, entry.original_name or ""))
        if role:
            out[role] = entry.entity_id
    return out


def can_retry(hass: HomeAssistant, device_id: str | None) -> bool:
    """Whether the device's satellite can listen again without the wake word (start_conversation)."""
    entity = satellite_entity(hass, device_id) if device_id else None
    st = hass.states.get(entity) if entity else None
    features = int(st.attributes.get("supported_features") or 0) if st else 0
    return bool(features & AssistSatelliteEntityFeature.START_CONVERSATION)


def device_status(
    hass: HomeAssistant, controls: dict[str, str], device_id: str | None = None
) -> dict[str, Any]:
    """Mute, Do not disturb and a ringing timer, from the controls' states; and tap to correct."""

    def state(role: str) -> str | None:
        st = hass.states.get(controls[role]) if role in controls else None
        return st.state if st is not None else None

    names = state("ringing_names")
    return {
        "mute": state("mute") == "on",
        "dnd": state("dnd") == "on",
        "ringing": state("ringing") == "on",
        "ringing_names": names if names not in (None, "unknown", "unavailable") else "",
        "can_stop": "stop" in controls,
        "can_retry": can_retry(hass, device_id),
    }


def device_timers(hass: HomeAssistant, device_ids: set[str | None]) -> list[dict[str, Any]]:
    """Running and paused voice timers of these devices (Home Assistant's timer manager).

    Read from ``hass.data``: the manager allows one handler per device and the Echo's integration holds it.
    """
    manager = hass.data.get(TIMER_DATA)
    if manager is None:
        return []
    now = time.time()
    out = []
    for timer in list(getattr(manager, "timers", {}).values()):
        if timer.device_id not in device_ids:
            continue
        left = timer.seconds_left
        out.append(
            {
                "id": timer.id,
                "device_id": timer.device_id,
                "name": timer.name or "",
                "total": timer.created_seconds,
                "left": left,
                "active": timer.is_active,
                "ends_at": round(now + left, 1) if timer.is_active else None,
            }
        )
    out.sort(key=lambda t: t["left"])
    return out


@websocket_api.websocket_command(
    {vol.Required("type"): "hermes_voice/screen/subscribe", vol.Optional("device_id"): vol.Any(str, None)}
)
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stream one device's turns and state (or every device's, without device_id)."""
    hub = async_get_hub(hass)
    if hub is None:
        connection.send_error(msg["id"], "not_ready", "Hermes Voice is not set up")
        return
    device_id: str | None = msg.get("device_id")
    msg_id = msg["id"]

    if device_id is not None:
        entities = [e] if (e := satellite_entity(hass, device_id)) else []
    else:
        registry = er.async_get(hass)
        entities = [s.entity_id for s in hass.states.async_all(SATELLITE) if registry.async_get(s.entity_id)]
    device_of = {
        entity_id: (reg.device_id if (reg := er.async_get(hass).async_get(entity_id)) else None)
        for entity_id in entities
    }

    @callback
    def forward(event: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg_id, event))

    @callback
    def on_state(event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is not None:
            forward(
                {"type": "state", "state": new.state, "device_id": device_of.get(event.data["entity_id"])}
            )

    devices = {d for d in device_of.values() if d}
    controls = {d: device_controls(hass, d) for d in devices}
    control_device = {eid: d for d, c in controls.items() for eid in c.values()}

    @callback
    def on_control(event: Event[EventStateChangedData]) -> None:
        dev = control_device[event.data["entity_id"]]
        forward({"type": "device", "device_id": dev, **device_status(hass, controls[dev], dev)})

    last_timers: list[Any] = [None]  # nothing sent yet: the first poll always sends, even an empty list

    @callback
    def poll_timers(_now: Any = None) -> None:
        timers = device_timers(hass, set(devices))
        key = [
            (
                t["id"],
                t["active"],
                t["name"],
                round((t["ends_at"] or 0) / 2),
                t["left"] if not t["active"] else 0,
            )
            for t in timers
        ]
        if [key] != last_timers:
            last_timers[:] = [key]
            forward({"type": "timers", "timers": timers, "device_id": device_id})

    unsubs = [hub.async_subscribe(device_id, forward)]
    if entities:
        unsubs.append(async_track_state_change_event(hass, entities, on_state))
    if control_device:
        unsubs.append(async_track_state_change_event(hass, list(control_device), on_control))
    if devices:
        unsubs.append(async_track_time_interval(hass, poll_timers, TIMER_POLL))

    @callback
    def unsubscribe() -> None:
        for unsub in unsubs:
            unsub()

    connection.subscriptions[msg_id] = unsubscribe
    connection.send_result(msg_id)
    states = {device_of[e]: (s.state if (s := hass.states.get(e)) else "unavailable") for e in entities}
    turns = {d: t.as_dict() for d, t in hub.turns.items() if device_id is None or d == device_id}
    forward(
        {
            "type": "snapshot",
            "states": states,
            "turns": turns,
            "device_id": device_id,
            "page": hub.page_version,
            "devices": {d: device_status(hass, c, d) for d, c in controls.items()},
            "cook": {
                d: c.as_dict()
                for d in list(hub.cooking)
                if (device_id is None or d == device_id) and (c := hub.get_cook(d)) is not None
            },
            "video": {d: v.as_dict() for d, v in hub.video.items() if device_id is None or d == device_id},
        }
    )
    poll_timers()


@websocket_api.websocket_command(
    {vol.Required("type"): "hermes_voice/screen/stop_timer", vol.Required("device_id"): str}
)
@websocket_api.async_response
async def ws_stop_timer(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stop a ringing timer on an Echo: presses its own "Stop ringing" button, nothing else."""
    stop = device_controls(hass, msg["device_id"]).get("stop")
    if stop is None:
        connection.send_error(msg["id"], "not_supported", "This device has no Stop ringing button")
        return
    await hass.services.async_call("button", "press", {"entity_id": stop}, blocking=True)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): "hermes_voice/screen/retry", vol.Required("device_id"): str}
)
@websocket_api.async_response
async def ws_retry(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Tap to correct: stop the current turn on an Echo and listen again, without the wake word.

    Home Assistant's start_conversation cancels the running pipeline (and so the Hermes request), says
    RETRY_MESSAGE and opens the microphone. The next request carries a note with what was misheard.
    """
    device_id = msg["device_id"]
    entity = satellite_entity(hass, device_id)
    if entity is None or not can_retry(hass, device_id):
        connection.send_error(msg["id"], "not_supported", "This device cannot listen again on its own")
        return
    hub = async_get_hub(hass)
    heard = hub.cancelled(device_id) if hub else ""
    data: dict[str, Any] = {"entity_id": entity, "start_message": RETRY_MESSAGE, "preannounce": False}
    if heard:
        data["extra_system_prompt"] = RETRY_PROMPT.format(heard=heard.replace('"', "'")[:300])
    try:
        await hass.services.async_call(SATELLITE, "start_conversation", data, blocking=True)
    except HomeAssistantError as err:  # the satellite was busy or went away: tell the screen
        LOGGER.warning("Tap to correct failed on %s: %s", entity, err)
        connection.send_error(msg["id"], "failed", str(err))
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): "hermes_voice/screen/overview", vol.Optional("entry_id"): str}
)
@websocket_api.async_response
async def ws_overview(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Hermes' jobs, recent sessions and status, through the integration's own key."""
    hub = async_get_hub(hass)
    if hub is None:
        connection.send_error(msg["id"], "not_ready", "Hermes Voice is not set up")
        return
    connection.send_result(msg["id"], await hub.async_overview(msg.get("entry_id")))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hermes_voice/screen/cook",
        vol.Required("device_id"): vol.Any(str, None),
        vol.Required("action"): vol.In(("open", "view", "step", "check", "close")),
        vol.Optional("recipe"): str,
        vol.Optional("view"): vol.In(("card", "ingredients", "steps")),
        vol.Optional("step"): vol.All(int, vol.Range(min=0)),
        vol.Optional("index"): vol.All(int, vol.Range(min=0)),
        vol.Optional("checked"): bool,
    }
)
@websocket_api.async_response
async def ws_cook(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The screen opens a recipe, switches between card, ingredients and steps, ticks off an ingredient, closes."""
    hub = async_get_hub(hass)
    if hub is None:
        connection.send_error(msg["id"], "not_ready", "Hermes Voice is not set up")
        return
    device_id, action = msg["device_id"], msg["action"]
    if action == "open":
        try:
            result = await hub.async_open_recipe(device_id, msg.get("recipe", ""), msg.get("view", "card"))
        except recipes.RecipeError as err:
            connection.send_error(msg["id"], "not_found", str(err))
            return
        connection.send_result(msg["id"], result)
        return
    if action == "close":
        hub.close_cook(device_id)
        connection.send_result(msg["id"])
        return
    cook = hub.get_cook(device_id)
    if cook is None:
        connection.send_error(msg["id"], "not_open", "No recipe is open on this device")
        return
    cook.at = time.monotonic()
    if action == "view":
        cook.view = msg.get("view", cook.view)
        if "step" in msg:
            cook.step = msg["step"]
    elif action == "step":
        cook.view = "steps"
        cook.step = msg.get("step", cook.step)
    elif action == "check" and "index" in msg:
        (cook.checked.add if msg.get("checked", True) else cook.checked.discard)(msg["index"])
    cook.step = max(0, min(cook.step, len(cook.recipe.get("steps") or [1]) - 1))
    hub.publish_cook(device_id)
    connection.send_result(msg["id"], cook.as_dict())


@websocket_api.websocket_command(
    {vol.Required("type"): "hermes_voice/screen/recipes", vol.Optional("query"): str}
)
@websocket_api.async_response
async def ws_recipes(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """For browsing on the screen: this week's meal plan and the newest recipes (or the ones matching words)."""
    if recipes.mealie_entry(hass) is None:
        connection.send_result(msg["id"], {"available": False, "plan": [], "items": []})
        return
    query = msg.get("query", "")

    async def safely(coro: Any) -> list[dict[str, Any]]:
        try:
            return list(await coro)
        except recipes.RecipeError:
            return []

    plan_task = safely(recipes.async_meal_plan(hass, dt_util.now().date())) if not query else None
    items = await safely(recipes.async_search(hass, query, 24))
    plan = await plan_task if plan_task else []
    connection.send_result(msg["id"], {"available": True, "plan": plan, "items": items})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hermes_voice/screen/start_timer",
        vol.Required("device_id"): str,
        vol.Required("seconds"): vol.All(int, vol.Range(min=1, max=24 * 3600)),
        vol.Optional("name"): str,
    }
)
@websocket_api.async_response
async def ws_start_timer(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """A step's timer button: a voice timer on the device, as if "set a timer for 7 minutes" was said to it."""
    slots: dict[str, Any] = {"seconds": {"value": msg["seconds"]}}
    if msg.get("name"):
        slots["name"] = {"value": msg["name"][:40]}
    try:
        await intent.async_handle(
            hass,
            DOMAIN,
            intent.INTENT_START_TIMER,
            slots,
            device_id=msg["device_id"],
            language=hass.config.language,
        )
    except intent.IntentError as err:
        connection.send_error(msg["id"], "failed", str(err) or "This device cannot run timers")
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hermes_voice/screen/video",
        vol.Required("device_id"): str,
        vol.Required("action"): vol.In(["on", "off", "keep"]),
        vol.Optional("why"): str,
    }
)
@callback
def ws_video(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """The screen's buttons ("turn the camera off", "keep it on"), and the Echo ending video by itself
    (shutter closed, camera cut off, no keep-alive): why says which."""
    hub = async_get_hub(hass)
    if hub is None:
        connection.send_error(msg["id"], "not_ready", "Hermes Voice is not set up")
        return
    device_id, action = msg["device_id"], msg["action"]
    if action == "on":
        hub.video_on(device_id, msg.get("why", "screen"))
    elif action == "off":
        hub.video_off(device_id, msg.get("why", "screen"))
    else:
        hub.video_keep(device_id)
    connection.send_result(msg["id"], {"on": device_id in hub.video})
