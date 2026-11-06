"""Live feed for a Hermes screen on an Echo Show or a browser (docs/show-screen.md).

The screen page gets everything through two Home Assistant websocket commands, so it needs only a
Home Assistant token (any user, not admin) and never the Hermes API key:

- ``hermes_voice/screen/subscribe``: the voice device's state (idle, listening, processing, responding),
  what was heard, Hermes' reply as it is written, its tools, and the answers Home Assistant gave itself.
- ``hermes_voice/screen/overview``: Hermes' scheduled jobs, recent sessions and gateway status.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import time
from typing import Any, ClassVar
from urllib.parse import urlsplit

from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.conversation.chat_log import async_subscribe_chat_logs
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
import voluptuous as vol

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
        self._overview: dict[str, tuple[float, dict[str, Any]]] = {}
        self.page_version = ""

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

    @callback
    def reply_done(self, device_id: str | None, text: str) -> None:
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        turn.reply, turn.phase = text[:MAX_TEXT], "done"
        self.publish(device_id, {"type": "reply_done", "text": turn.reply})

    @callback
    def error(self, device_id: str | None, message: str) -> None:
        turn = self.turns.setdefault(device_id, Turn(at=time.time()))
        turn.phase = "error"
        self.publish(device_id, {"type": "error", "message": message})

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
                }
            else:
                out["health"] = _pick(res, HEALTH_FIELDS)
        self._overview[entry.entry_id] = (time.monotonic(), out)
        return out


class ScreenRedirectView(HomeAssistantView):
    """``/hermes_voice/screen`` opens the page (a folder URL alone is not served)."""

    url = SCREEN_URL
    extra_urls: ClassVar[list[str]] = [SCREEN_URL + "/"]
    name = "hermes_voice:screen"
    requires_auth = False  # the page logs in to the websocket itself

    async def get(self, request: web.Request) -> web.Response:
        query = f"?{request.query_string}" if request.query_string else ""
        raise web.HTTPFound(f"{SCREEN_URL}/index.html{query}")


def _page_version() -> str:
    digest = hashlib.sha256()
    for name in PAGE_FILES:
        path = SCREEN_DIR / name
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


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
    hub.page_version = await hass.async_add_executor_job(_page_version)
    hass.data[DATA_SCREEN] = hub
    async_subscribe_chat_logs(hass, hub.on_chat_log)
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_overview)
    if SCREEN_DIR.is_dir() and getattr(hass, "http", None) is not None:
        hass.http.register_view(ScreenRedirectView())
        await hass.http.async_register_static_paths([StaticPathConfig(SCREEN_URL, str(SCREEN_DIR), False)])


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

    unsubs = [hub.async_subscribe(device_id, forward)]
    if entities:
        unsubs.append(async_track_state_change_event(hass, entities, on_state))

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
        }
    )


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
