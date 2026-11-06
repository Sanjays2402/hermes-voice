"""Conversation agent: session modes, idle reset, headers, cleanup and error paths."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_voice.const import (
    CONF_FALLBACK_MESSAGE,
    CONF_IDLE_RESET_MINUTES,
    CONF_SEND_TIME,
    CONF_SESSION_MODE,
    CONF_USER_KEY,
    CONF_VOICE_CLEANUP,
    CONF_VOICE_INSTRUCTION,
    DEFAULT_FALLBACK_MESSAGE,
    DEFAULT_VOICE_INSTRUCTION,
    SESSION_PER_DEVICE,
)

from .conftest import BASE, KEY, MODELS, make_entry, reply

AGENT = "conversation.voice_spike"
CHAT = f"{BASE}/chat/completions"


async def _setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, options: dict[str, Any] | None = None
):
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    entry = make_entry(options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _chat_calls(aioclient_mock: AiohttpClientMocker) -> list[tuple[dict[str, Any], dict[str, str]]]:
    return [(c[2], c[3]) for c in aioclient_mock.mock_calls if c[0] == "POST"]


async def _say(
    hass: HomeAssistant,
    text: str,
    conversation_id: str | None = None,
    device_id: str | None = None,
    user_id: str | None = "user1",
) -> conversation.ConversationResult:
    return await conversation.async_converse(
        hass, text, conversation_id, Context(user_id=user_id), agent_id=AGENT, device_id=device_id
    )


def _speech(result: conversation.ConversationResult) -> str:
    # Streamed sentences are separated by blank lines for Piper; compare the words.
    return " ".join(result.response.speech["plain"]["speech"].split())


async def test_entity_registered(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The entry exposes one conversation entity."""
    await _setup(hass, aioclient_mock)
    assert hass.states.get(AGENT) is not None


async def test_per_conversation_session(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Each turn sends X-Hermes-Session-Id=ha-<conversation_id> and only the new user message."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(CHAT, json=reply("Acknowledged, captain."))
    r1 = await _say(hass, "My codeword is PELICAN.")
    cid = r1.conversation_id
    r2 = await _say(hass, "What is my codeword?", cid)
    assert r2.conversation_id == cid
    calls = _chat_calls(aioclient_mock)
    assert len(calls) == 2
    for (body, headers), text in zip(calls, ["My codeword is PELICAN.", "What is my codeword?"], strict=True):
        assert headers["X-Hermes-Session-Id"] == f"ha-{cid}"
        assert headers["Authorization"] == f"Bearer {KEY}"
        assert "X-Hermes-Session-Key" not in headers
        assert body["stream"] is True  # streaming is the default
        assert body["model"] == "voice-spike"
        # system (voice instruction, then the local time) + only the latest user message: Hermes holds the history.
        system, user = body["messages"]
        assert system["role"] == "system"
        assert system["content"].startswith(DEFAULT_VOICE_INSTRUCTION + "\nCurrent local date and time: ")
        assert user == {"role": "user", "content": text}


async def test_same_opening_phrase_does_not_collide(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Two new conversations starting with the same phrase get different Hermes sessions."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(CHAT, json=reply())
    a = await _say(hass, "What time is it?")
    b = await _say(hass, "What time is it?")
    assert a.conversation_id != b.conversation_id
    sids = [h["X-Hermes-Session-Id"] for _, h in _chat_calls(aioclient_mock)]
    assert sids == [f"ha-{a.conversation_id}", f"ha-{b.conversation_id}"]


async def test_per_device_window_and_idle_reset(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """per_device: same device keeps one session across conversations until idle_reset_minutes pass."""
    await _setup(hass, aioclient_mock, {CONF_SESSION_MODE: SESSION_PER_DEVICE, CONF_IDLE_RESET_MINUTES: 30})
    aioclient_mock.post(CHAT, json=reply())
    await _say(hass, "one", device_id="dev1")
    freezer.tick(timedelta(minutes=20))
    await _say(hass, "two", device_id="dev1")  # new HA conversation, same window
    freezer.tick(timedelta(minutes=29))
    await _say(hass, "three", device_id="dev1")  # still within 30 min of the last turn
    await _say(hass, "other device", device_id="dev2")
    freezer.tick(timedelta(minutes=31))
    await _say(hass, "four", device_id="dev1")  # idle > 30 min -> new window
    sids = [h["X-Hermes-Session-Id"] for _, h in _chat_calls(aioclient_mock)]
    assert sids[0] == sids[1] == sids[2]
    assert sids[0].startswith("ha-dev-dev1-")
    assert sids[3].startswith("ha-dev-dev2-")
    assert sids[4].startswith("ha-dev-dev1-") and sids[4] != sids[0]


async def test_per_device_without_device_uses_user(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Typed/REST turns have no device: the HA user is the key, then the conversation id."""
    await _setup(hass, aioclient_mock, {CONF_SESSION_MODE: SESSION_PER_DEVICE})
    aioclient_mock.post(CHAT, json=reply())
    await _say(hass, "hi")
    await _say(hass, "again")
    r = await _say(hass, "anon", user_id=None)
    sids = [h["X-Hermes-Session-Id"] for _, h in _chat_calls(aioclient_mock)]
    assert sids[0] == sids[1] and sids[0].startswith("ha-dev-user-user1-")
    assert sids[2].startswith(f"ha-dev-conv-{r.conversation_id}-")


async def test_per_device_window_persists(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict,
) -> None:
    """Window state is written to HA storage and reloaded after a restart of the entry."""
    entry = await _setup(hass, aioclient_mock, {CONF_SESSION_MODE: SESSION_PER_DEVICE})
    aioclient_mock.post(CHAT, json=reply())
    await _say(hass, "one", device_id="dev1")
    freezer.tick(timedelta(seconds=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    key = f"hermes_voice.device_windows.{entry.entry_id}"
    assert "dev1" in hass_storage[key]["data"]["windows"]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _say(hass, "two", device_id="dev1")
    sids = [h["X-Hermes-Session-Id"] for _, h in _chat_calls(aioclient_mock)]
    assert sids[0] == sids[1]


async def test_user_key_header(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """user_key is rendered and sent as X-Hermes-Session-Key."""
    await _setup(hass, aioclient_mock, {CONF_USER_KEY: "ha:{user_id}:{device_id}"})
    aioclient_mock.post(CHAT, json=reply())
    await _say(hass, "hi", device_id="dev9")
    _, headers = _chat_calls(aioclient_mock)[0]
    assert headers["X-Hermes-Session-Key"] == "ha:user1:dev9"


async def test_custom_and_empty_voice_instruction(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An empty voice instruction sends only the user message."""
    await _setup(hass, aioclient_mock, {CONF_VOICE_INSTRUCTION: "", CONF_SEND_TIME: False})
    aioclient_mock.post(CHAT, json=reply())
    await _say(hass, "hi")
    body, _ = _chat_calls(aioclient_mock)[0]
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_local_time_sent(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """Hermes has no clock: each turn carries Home Assistant's local date and time."""
    await hass.config.async_set_time_zone("America/Chicago")
    freezer.move_to("2026-11-05 21:47:00+00:00")
    await _setup(hass, aioclient_mock, {CONF_VOICE_INSTRUCTION: ""})
    aioclient_mock.post(CHAT, json=reply())
    await _say(hass, "What time is it?")
    freezer.move_to("2026-11-06 05:05:00+00:00")
    await _say(hass, "And now?")
    systems = [body["messages"][0] for body, _ in _chat_calls(aioclient_mock)]
    assert systems == [
        {
            "role": "system",
            "content": "Current local date and time: Thursday, November 5, 2026, 3:47 PM (America/Chicago).",
        },
        {
            "role": "system",
            "content": "Current local date and time: Thursday, November 5, 2026, 11:05 PM (America/Chicago).",
        },
    ]


async def test_cleanup_applied_and_toggle(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Markdown/URLs are cleaned by default; the toggle turns it off."""
    raw = "**Sure**, captain! See https://example.com/x 🚀\n\n- one\n- two"
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.post(CHAT, json=reply(raw))
    assert _speech(await _say(hass, "hi")) == "Sure, captain! See link. one. two"
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_VOICE_CLEANUP: False})
    await hass.async_block_till_done()
    off = await _say(hass, "hi")
    assert off.response.speech["plain"]["speech"] == raw  # passed on exactly as written


ERROR_CASES = {
    "http_429": {"status": 429, "json": {"error": {"message": "rate limited"}}},
    "rate_limit_text_200": {
        "json": reply(
            "ChatGPT or Codex Subscription rate-limited ... HTTP 429: The usage limit has been reached",
            hermes={"completed": False, "partial": False, "failed": True, "error": "HTTP 429: usage limit"},
        )
    },
    "finish_reason_error": {
        "json": {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Traceback boom"},
                    "finish_reason": "error",
                }
            ]
        }
    },
    "agent_incomplete_502": {
        "status": 502,
        "json": {"error": {"message": "Agent run did not produce a response.", "code": "agent_incomplete"}},
    },
    "agent_incomplete_502_rate_limit": {
        "status": 502,
        "json": {"error": {"message": "HTTP 429 usage limit"}},
    },
    "http_500": {"status": 500, "text": "Internal Server Error"},
    "http_401": {"status": 401, "json": {"error": {"message": "bad key"}}},
    "http_404": {"status": 404, "text": "nope"},
    "timeout": {"exc": TimeoutError()},
    "connection_error": {"exc": aiohttp.ClientConnectionError("refused")},
    "empty_reply": {"json": reply("")},
    "malformed": {"json": {"unexpected": True}},
    "not_json": {"text": "<html>proxy error</html>"},
    "provider_auth_text_200": {
        "json": reply(
            "Provider authentication failed: No Codex credentials stored. Run hermes -p x auth add openai-codex"
        )
    },
    "provider_rate_text_200": {"json": reply("Provider rate-limited: usage limit reached, resets in 2 h")},
}


@pytest.mark.parametrize("case", list(ERROR_CASES))
async def test_error_paths_speak_fallback(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Every failure speaks the fallback, never raw error text."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(CHAT, **ERROR_CASES[case])
    result = await _say(hass, "hello")
    assert _speech(result) == DEFAULT_FALLBACK_MESSAGE
    assert "Hermes turn failed" in caplog.text
    assert KEY not in caplog.text


async def test_custom_fallback_message(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The fallback text is configurable."""
    await _setup(hass, aioclient_mock, {CONF_FALLBACK_MESSAGE: "Brain offline."})
    aioclient_mock.post(CHAT, exc=TimeoutError())
    assert _speech(await _say(hass, "hello")) == "Brain offline."


async def test_setup_not_ready(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Gateway down at startup -> retry later."""
    aioclient_mock.get(f"{BASE}/models", exc=aiohttp.ClientConnectionError())
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    from homeassistant.config_entries import ConfigEntryState

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Unloading removes the agent."""
    entry: MockConfigEntry = await _setup(hass, aioclient_mock)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(AGENT).state == "unavailable"
