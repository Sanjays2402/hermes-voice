"""Video mode on the screen (video.py): on when asked or when Hermes looks, a nudge, off when asked or idle."""

from __future__ import annotations

from datetime import timedelta
import time

from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.hermes_voice import video
from custom_components.hermes_voice.screen import async_get_hub

from .test_recipes import _subscribe
from .test_screen import AGENT, CHAT, _echo, _events, _setup
from .test_streaming import SSE, chunk, stream, tool


@pytest.mark.parametrize(
    ("said", "action"),
    [
        ("Video mode", "on"),
        ("Hey Hermes, start video.", "on"),
        ("Let's video chat", "on"),
        ("Turn on the camera", "on"),
        ("Stop video", "off"),
        ("End the video call.", "off"),
        ("Turn off the camera, please", "off"),
        ("We're done with the camera", "off"),
        ("Yes, camera off", "off"),
        ("No more video", "off"),
        ("Hey Hermes, no more video", "off"),
        ("No, stop video", "off"),
        ("Keep the camera on", "keep"),
        ("Not done yet", "keep"),
        ("What am I holding?", None),
        ("How do I turn off the camera on my phone?", None),
        ("What's the weather on Tuesday?", None),
    ],
)
def test_parse_command(said: str, action: str | None) -> None:
    assert video.parse_command(said) == action


def test_nudge_after_replies_without_the_camera() -> None:
    v = video.VideoMode()
    assert [v.turn_done(looked) for looked in (False, False, True, False, False)] == [False] * 5
    assert v.turn_done(False) is True  # third in a row
    assert v.turn_done(False) is False  # asked once
    v.keep()
    assert (v.misses, v.asked) == (0, False)


def _look_turn(aioclient_mock: AiohttpClientMocker, looked: bool) -> None:
    aioclient_mock.clear_requests()
    frames = (
        [
            tool("c1", "running", name="mcp__eye__look", label="what they hold"),
            tool("c1", "completed", name="mcp__eye__look"),
        ]
        if looked
        else []
    )
    aioclient_mock.post(CHAT, text=stream(*frames, chunk("Sure."), chunk(finish="stop")), headers=SSE)


async def test_video_mode_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """Hermes looks: video turns on and Hermes is told. Three replies without the camera: the screen asks.
    "Stop video" is answered here. Idle: it ends by itself."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    ws, snapshot = await _subscribe(hass_ws_client, hass, device_id)
    assert snapshot["video"] == {}

    _look_turn(aioclient_mock, looked=True)
    await conversation.async_converse(
        hass, "What am I holding?", "c-1", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    events = await _events(ws, 5)  # heard, tool_start, video on, tool_end, delta... (order checked below)
    events += await _events(ws, 1)
    assert {"type": "video", "on": True, "why": "look", "device_id": device_id} in events

    for n, said in enumerate(("Weather Tuesday?", "Directions to the gym?", "My Thursday?"), 2):
        _look_turn(aioclient_mock, looked=False)
        await conversation.async_converse(
            hass, said, f"c-{n}", Context(user_id="u"), agent_id=AGENT, device_id=device_id
        )
        messages = aioclient_mock.mock_calls[-1][2]["messages"]
        assert "Video mode is on" in messages[-1]["content"]
        assert not any("Video mode" in m["content"] for m in messages if m["role"] == "system")
        got = await _events(ws, 3)  # heard, delta, reply_done
        if n == 4:
            got += await _events(ws, 1)
            assert got[-1] == {"type": "video", "on": True, "ask": True, "device_id": device_id}

    await ws.send_json(
        {"id": 5, "type": "hermes_voice/screen/video", "device_id": device_id, "action": "keep"}
    )
    assert (await ws.receive_json())["event"] == {
        "type": "video",
        "on": True,
        "why": "keep",
        "device_id": device_id,
    }
    assert (await ws.receive_json())["result"] == {"on": True}

    posts = len([c for c in aioclient_mock.mock_calls if c[0] == "POST"])
    result = await conversation.async_converse(
        hass, "Stop video", "c-9", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    assert result.response.speech["plain"]["speech"] == "Camera's off."
    assert len([c for c in aioclient_mock.mock_calls if c[0] == "POST"]) == posts  # Hermes was not asked
    events = await _events(ws, 2)
    assert events[0] == {"type": "video", "on": False, "why": "voice", "device_id": device_id}
    assert events[1]["type"] == "local_answer"

    result = await conversation.async_converse(
        hass, "video mode", "c-10", Context(user_id="u"), agent_id=AGENT, device_id=device_id
    )
    assert result.response.speech["plain"]["speech"] == "Video's on."
    assert (await _events(ws, 1))[0]["why"] == "voice"
    await _events(ws, 1)  # local_answer

    # Five idle minutes: it ends by itself.
    hub = async_get_hub(hass)
    assert hub is not None
    hub.video[device_id].last = time.monotonic() - video.VIDEO_IDLE - 1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert (await _events(ws, 1))[0] == {
        "type": "video",
        "on": False,
        "why": "timeout",
        "device_id": device_id,
    }
    assert device_id not in hub.video


async def test_screen_reports_the_echo_ended_video(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
) -> None:
    """The shutter closed on the Echo: its screen says so, and video mode ends (and shows in a snapshot)."""
    entry = await _setup(hass, aioclient_mock)
    device_id, _ = _echo(hass, entry)
    ws, _ = await _subscribe(hass_ws_client, hass, device_id)
    await ws.send_json({"id": 2, "type": "hermes_voice/screen/video", "device_id": device_id, "action": "on"})
    assert (await ws.receive_json())["event"]["on"] is True
    assert (await ws.receive_json())["result"] == {"on": True}
    _, snapshot = await _subscribe(hass_ws_client, hass, device_id)
    assert snapshot["video"] == {device_id: {"on": True, "asked": False}}
    await ws.send_json(
        {
            "id": 3,
            "type": "hermes_voice/screen/video",
            "device_id": device_id,
            "action": "off",
            "why": "covered",
        }
    )
    assert (await ws.receive_json())["event"] == {
        "type": "video",
        "on": False,
        "why": "covered",
        "device_id": device_id,
    }
    assert (await ws.receive_json())["result"] == {"on": False}
