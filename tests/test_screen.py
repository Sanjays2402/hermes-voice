"""Screen feed (docs/show-screen.md): live turn events, satellite state, local answers, overview."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import aiohttp
from homeassistant.components import conversation
from homeassistant.components.intent.const import TIMER_DATA
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import chat_session, device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.hermes_voice.const import CONF_STREAM

from .conftest import BASE, KEY, MODELS, make_entry, reply
from .test_streaming import SSE, chunk, stream, tool

AGENT = "conversation.voice_spike"
CHAT = f"{BASE}/chat/completions"
ROOT = BASE.removesuffix("/v1")


async def _setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    entry = make_entry(options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _echo(hass: HomeAssistant, entry: MockConfigEntry, state: str = "idle") -> tuple[str, str]:
    """A device with an assist_satellite entity, like an Echo running echo-dot-assist."""
    other = MockConfigEntry(domain="esphome")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("esphome", "echo")}
    )
    ent = er.async_get(hass).async_get_or_create(
        "assist_satellite", "esphome", "echo-sat", device_id=device.id, config_entry=other
    )
    hass.states.async_set(ent.entity_id, state)
    return device.id, ent.entity_id


async def _events(ws: Any, n: int) -> list[dict[str, Any]]:
    return [(await ws.receive_json())["event"] for _ in range(n)]


async def test_stream_turn_reaches_screen(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """A screen watching the Echo gets what was heard, tools, the reply as written, and the end."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    aioclient_mock.post(
        CHAT,
        text=stream(
            tool("c1", "running"),
            tool("c1", "completed"),
            chunk("**Sunny**, "),
            chunk("see example.com."),
            chunk(finish="stop"),
        ),
        headers=SSE,
    )
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    assert (await ws.receive_json())["success"]
    snapshot = (await ws.receive_json())["event"]
    assert snapshot["type"] == "snapshot"
    assert snapshot["states"] == {device_id: "idle"}
    assert (await ws.receive_json())["event"] == {"type": "timers", "timers": [], "device_id": device_id}
    assert len(snapshot["page"]) == 12  # the page's version: a screen reloads when it changes

    await conversation.async_converse(
        hass, "Weather?", None, Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    events = await _events(ws, 6)
    assert [e["type"] for e in events] == ["heard", "tool_start", "tool_end", "delta", "delta", "reply_done"]
    assert events[0]["text"] == "Weather?"
    assert events[1]["tool"] == "web_search" and events[1]["label"] == "weather Dallas"
    # The screen gets the reply as Hermes wrote it (markdown, links), not the cleaned spoken text.
    assert events[5]["text"] == "**Sunny**, see example.com."
    assert all(e["device_id"] == device_id for e in events)


async def test_other_device_and_snapshot(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """A screen does not see other devices' turns; a late screen gets the last turn in its snapshot."""
    entry = await _setup(hass, aioclient_mock, {CONF_STREAM: False})
    device_id, _ = _echo(hass, entry)
    aioclient_mock.post(CHAT, json=reply("Hi there."))
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    await ws.receive_json()
    await ws.receive_json()  # snapshot
    await ws.receive_json()  # timers (none)
    await conversation.async_converse(
        hass, "elsewhere", None, Context(user_id="u"), agent_id=AGENT, device_id="other"
    )
    await conversation.async_converse(
        hass, "here", None, Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    events = await _events(ws, 3)
    assert [(e["type"], e.get("text")) for e in events] == [
        ("heard", "here"),
        ("delta", "Hi there."),
        ("reply_done", "Hi there."),
    ]

    ws2 = await hass_ws_client(hass)
    await ws2.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    await ws2.receive_json()
    snapshot = (await ws2.receive_json())["event"]
    turn = snapshot["turns"][device_id]
    assert (turn["heard"], turn["reply"], turn["phase"], turn["by"]) == (
        "here",
        "Hi there.",
        "done",
        "hermes",
    )


async def test_satellite_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """The Echo's listening/processing/responding state reaches its screen."""
    entry = await _setup(hass, aioclient_mock)
    device_id, entity_id = _echo(hass, entry)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    await ws.receive_json()
    await ws.receive_json()  # snapshot
    await ws.receive_json()  # timers (none)
    for state in ("listening", "processing", "responding", "idle"):
        hass.states.async_set(entity_id, state)
        await hass.async_block_till_done()
    events = await _events(ws, 4)
    assert [e["state"] for e in events] == ["listening", "processing", "responding", "idle"]
    assert {e["device_id"] for e in events} == {device_id}


async def test_local_answer(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """A turn Home Assistant answers itself is shown too, on the Echo that is mid-turn."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry, state="processing")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe"})
    await ws.receive_json()
    await ws.receive_json()  # snapshot
    await ws.receive_json()  # timers (none)
    # As the Assist pipeline does when "Prefer handling commands locally" answers a sentence itself.
    user_input = conversation.ConversationInput(
        text="What time is it?",
        context=Context(),
        conversation_id=None,
        device_id=device_id,
        satellite_id=None,
        language="en",
        agent_id=conversation.HOME_ASSISTANT_AGENT,
    )
    with (
        chat_session.async_get_chat_session(hass) as session,
        conversation.async_get_chat_log(hass, session, user_input) as chat_log,
    ):
        # HA first records its intent as a tool call under the pipeline's agent (Hermes), then answers.
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=AGENT, content=None)
        )
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=conversation.HOME_ASSISTANT_AGENT, content="3:53 PM")
        )
    event = (await ws.receive_json())["event"]
    assert event == {
        "type": "local_answer",
        "heard": "What time is it?",
        "text": "3:53 PM",
        "device_id": device_id,
    }


async def test_overview(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Jobs, sessions and health come through the integration; private fields stay behind; cached."""
    await _setup(hass, aioclient_mock, {"screen_activity": "off"})
    aioclient_mock.get(
        f"{ROOT}/api/jobs",
        json={"jobs": [{"id": "j1", "name": "Morning brief", "next_run_at": "t", "prompt": "secret prompt"}]},
    )
    aioclient_mock.get(
        f"{ROOT}/api/sessions?limit=8",
        json={
            "data": [
                {"id": "s1", "title": "Christmas", "preview": "How many days", "estimated_cost_usd": 0.1}
            ]
        },
    )
    aioclient_mock.get(f"{ROOT}/health/detailed", status=500)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/overview"})
    result = (await ws.receive_json())["result"]
    assert result["jobs"] == [{"id": "j1", "name": "Morning brief", "next_run_at": "t"}]
    assert result["sessions"] == [{"id": "s1", "title": "Christmas", "preview": "How many days"}]
    assert "health" not in result and "health" in result["errors"]
    calls = len(aioclient_mock.mock_calls)
    await ws.send_json({"id": 2, "type": "hermes_voice/screen/overview"})
    assert (await ws.receive_json())["result"] == result
    assert len(aioclient_mock.mock_calls) == calls  # served from the cache
    # The Hermes key is used by Home Assistant, never sent to the screen.
    assert all("Authorization" not in str(v) for v in result.values())


async def test_screen_page_served(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_client: Any
) -> None:
    """The page is served uncached at every address a screen may have saved, with versioned file addresses."""
    await _setup(hass, aioclient_mock)
    client = await hass_client()
    for path in (
        "/hermes_voice/screen?device=abc",
        "/hermes_voice/screen/",
        "/hermes_voice/screen/index.html",
    ):
        resp = await client.get(path)
        assert resp.status == 200, path
        assert "no-store" in resp.headers["Cache-Control"]
        page = await resp.text()
        assert (
            'src="/hermes_voice/screen/app.js?v=' in page
            and 'href="/hermes_voice/screen/style.css?v=' in page
        )
    version = page.split("app.js?v=")[1].split('"')[0]
    assert len(version) == 12
    for asset in (f"app.js?v={version}", f"style.css?v={version}", "avatar-default.svg"):
        assert (await client.get(f"/hermes_voice/screen/{asset}")).status == 200


async def test_overview_activity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Background work comes from the activity helper on the gateway's host, with the entry's key; scope from options."""
    entry = await _setup(hass, aioclient_mock)
    for path in ("/api/jobs", "/api/sessions?limit=8", "/health/detailed"):
        aioclient_mock.get(f"{ROOT}{path}", json={})
    aioclient_mock.get(
        "http://hermes.test:8660/activity?scope=profile",
        json={
            "scope": "profile",
            "running": [
                {"id": "t1", "title": "Research", "assignee": "voice", "started_at": 1, "body": "secret"}
            ],
            "attention": [],
            "recent": [{"id": "t5", "title": "Digest", "completed_at": 2}],
            "next_up": {"text": "Review the draft", "at": 3, "extra": "dropped"},
        },
    )
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/overview"})
    result = (await ws.receive_json())["result"]
    assert result["activity"] == {
        "scope": "profile",
        "running": [{"id": "t1", "title": "Research", "assignee": "voice", "started_at": 1}],
        "attention": [],
        "recent": [{"id": "t5", "title": "Digest", "completed_at": 2}],
        "next_up": {"text": "Review the draft", "at": 3},
    }
    call = next(c for c in aioclient_mock.mock_calls if "8660" in str(c[1]))
    assert call[3]["Authorization"] == f"Bearer {KEY}"
    assert entry.options["screen_activity"] == "profile"  # the default


async def test_overview_activity_off_and_all(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Off asks nothing; "all" asks for every profile; a missing helper is an error, not a failure."""
    await _setup(hass, aioclient_mock, {"screen_activity": "off"})
    for path in ("/api/jobs", "/api/sessions?limit=8", "/health/detailed"):
        aioclient_mock.get(f"{ROOT}{path}", json={})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/overview"})
    result = (await ws.receive_json())["result"]
    assert "activity" not in result and "activity" not in result["errors"]
    assert not any("8660" in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_overview_activity_all_helper_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    await _setup(hass, aioclient_mock, {"screen_activity": "all", "activity_port": 9001})
    for path in ("/api/jobs", "/api/sessions?limit=8", "/health/detailed"):
        aioclient_mock.get(f"{ROOT}{path}", json={})
    aioclient_mock.get("http://hermes.test:9001/activity?scope=all", exc=aiohttp.ClientConnectionError())
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/overview"})
    result = (await ws.receive_json())["result"]
    assert "activity" not in result and "activity" in result["errors"]


async def test_overview_health_warnings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """A degraded gateway says which check is not ok (here: a nearly full disk)."""
    await _setup(hass, aioclient_mock, {"screen_activity": "off"})
    aioclient_mock.get(f"{ROOT}/api/jobs", json={})
    aioclient_mock.get(f"{ROOT}/api/sessions?limit=8", json={})
    aioclient_mock.get(
        f"{ROOT}/health/detailed",
        json={
            "status": "degraded",
            "readiness": {
                "checks": {"disk": {"status": "degraded", "used_percent": 97.5}, "model": {"status": "ok"}}
            },
        },
    )
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/overview"})
    health = (await ws.receive_json())["result"]["health"]
    assert health["status"] == "degraded"
    assert health["warnings"] == [{"check": "disk", "status": "degraded", "used_percent": 97.5}]


def _controls(hass: HomeAssistant, device_id: str, config_entry: MockConfigEntry) -> dict[str, str]:
    """The entities hassmic exposes for an Echo (by original name), with states."""
    registry = er.async_get(hass)
    out = {}
    for domain, name, key, state in (
        ("switch", "Mute", "mute", "off"),
        ("switch", "Do not disturb", "dnd", "on"),
        ("binary_sensor", "Timer ringing", "ringing", "off"),
        ("sensor", "Ringing timers", "names", ""),
        ("button", "Stop ringing", "stop", "unknown"),
    ):
        ent = registry.async_get_or_create(
            domain,
            "esphome",
            f"echo-{key}",
            device_id=device_id,
            config_entry=config_entry,
            original_name=name,
        )
        hass.states.async_set(ent.entity_id, state)
        out[key] = ent.entity_id
    return out


async def test_device_status_and_stop(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Mute, Do not disturb and a ringing timer reach the screen; Stop presses the Echo's own button."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    esphome = next(e for e in hass.config_entries.async_entries("esphome"))
    ids = _controls(hass, device_id, esphome)
    pressed: list[str] = []

    async def press(call: Any) -> None:
        pressed.extend(
            call.data["entity_id"] if isinstance(call.data["entity_id"], list) else [call.data["entity_id"]]
        )

    hass.services.async_register("button", "press", press)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    await ws.receive_json()
    snapshot = (await ws.receive_json())["event"]
    assert snapshot["devices"][device_id] == {
        "mute": False,
        "dnd": True,
        "ringing": False,
        "ringing_names": "",
        "can_stop": True,
    }
    await ws.receive_json()  # the first timers event (none)
    hass.states.async_set(ids["names"], "Pasta")
    hass.states.async_set(ids["ringing"], "on")
    await hass.async_block_till_done()
    events = [(await ws.receive_json())["event"] for _ in range(2)]
    assert (
        events[-1]["type"] == "device"
        and events[-1]["ringing"] is True
        and events[-1]["ringing_names"] == "Pasta"
    )

    ws2 = await hass_ws_client(hass)
    await ws2.send_json({"id": 1, "type": "hermes_voice/screen/stop_timer", "device_id": device_id})
    assert (await ws2.receive_json())["success"]
    assert pressed == [ids["stop"]]
    await ws2.send_json({"id": 2, "type": "hermes_voice/screen/stop_timer", "device_id": "no-such-device"})
    assert (await ws2.receive_json())["error"]["code"] == "not_supported"


async def test_timers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Voice timers of the followed Echo are sent with their end time; others are not."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    timer = SimpleNamespace(
        id="t1", device_id=device_id, name="Pasta", created_seconds=300, seconds_left=290, is_active=True
    )
    other = SimpleNamespace(
        id="t2", device_id="elsewhere", name="", created_seconds=60, seconds_left=50, is_active=True
    )
    hass.data[TIMER_DATA] = SimpleNamespace(timers={"t1": timer, "t2": other})
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "hermes_voice/screen/subscribe", "device_id": device_id})
    await ws.receive_json()
    await ws.receive_json()  # snapshot
    event = (await ws.receive_json())["event"]
    assert event["type"] == "timers"
    assert [(t["id"], t["name"], t["total"], t["left"], t["active"]) for t in event["timers"]] == [
        ("t1", "Pasta", 300, 290, True)
    ]
    assert event["timers"][0]["ends_at"] > 0
