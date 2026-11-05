"""Screen feed (docs/show-screen.md): live turn events, satellite state, local answers, overview."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.components import conversation
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
    await ws.receive_json()
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
    await ws.receive_json()
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
    """The screen page is served by Home Assistant."""
    await _setup(hass, aioclient_mock)
    client = await hass_client()
    resp = await client.get("/hermes_voice/screen?device=abc")
    assert resp.status == 200
    assert str(resp.url).endswith("/hermes_voice/screen/index.html?device=abc")
    page = await resp.text()
    assert "app.js" in page and "Hermes screen" in page
    for asset in ("app.js", "style.css", "avatar-default.svg"):
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
