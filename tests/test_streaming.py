"""Streaming replies: sentence chunks, Hermes' own tools, working phrase and errors."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components import conversation
from homeassistant.components.conversation.chat_log import DATA_CHAT_LOGS
from homeassistant.core import Context, HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_voice.cleanup import VoiceChunker
from custom_components.hermes_voice.const import (
    CONF_STREAM,
    CONF_VOICE_CLEANUP,
    CONF_WORKING_PHRASE,
    DEFAULT_FALLBACK_MESSAGE,
    DEFAULT_WORKING_PHRASE,
)

from .conftest import BASE, MODELS, make_entry, reply

AGENT = "conversation.voice_spike"
CHAT = f"{BASE}/chat/completions"
SSE = {"Content-Type": "text/event-stream"}


def chunk(content: str | None = None, finish: str | None = None, **extra: Any) -> str:
    delta = {"content": content} if content is not None else {}
    body = {
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    body.update(extra)
    return f"data: {json.dumps(body)}\n\n"


def tool(call_id: str, status: str, name: str = "web_search", label: str = "weather Dallas") -> str:
    data = {"tool": name, "toolCallId": call_id, "status": status, "label": label}
    return f"event: hermes.tool.progress\ndata: {json.dumps(data)}\n\n"


def stream(*frames: str) -> str:
    return (
        chunk().replace('"delta": {}', '"delta": {"role": "assistant"}')
        + ": keepalive\n\n"
        + "".join(frames)
        + ("data: [DONE]\n\n")
    )


async def _setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, options: dict[str, Any] | None = None
):
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    entry = make_entry(options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _say(hass: HomeAssistant, text: str = "hi") -> conversation.ConversationResult:
    return await conversation.async_converse(hass, text, None, Context(user_id="u"), agent_id=AGENT)


def _speech(result: conversation.ConversationResult) -> str:
    # Sentences are separated by blank lines for Piper (see test_sentences_end_with_blank_line).
    return " ".join(result.response.speech["plain"]["speech"].split())


def _log(hass: HomeAssistant, result: conversation.ConversationResult) -> list[Any]:
    return hass.data[DATA_CHAT_LOGS][result.conversation_id].content


def _assistant_text(hass: HomeAssistant, result: conversation.ConversationResult) -> str:
    return " ".join(
        " ".join((c.content or "").split())
        for c in _log(hass, result)
        if isinstance(c, conversation.AssistantContent) and c.content
    )


async def test_entity_supports_streaming(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """On by default, off with the option."""
    entry = await _setup(hass, aioclient_mock)
    ent = hass.data["entity_components"]["conversation"].get_entity(AGENT)
    assert ent.supports_streaming is True
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_STREAM: False})
    await hass.async_block_till_done()
    ent = hass.data["entity_components"]["conversation"].get_entity(AGENT)
    assert ent.supports_streaming is False


async def test_stream_in_sentences_cleaned(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Deltas split mid-word are joined; markdown is cleaned per sentence."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(
        CHAT,
        text=stream(
            chunk("It is **sun"),
            chunk("ny** in Dal"),
            chunk("las. See https://x.test/a "),
            chunk("now."),
            chunk(finish="stop"),
        ),
        headers=SSE,
    )
    result = await _say(hass)
    assert _speech(result) == "It is sunny in Dallas. See link now."
    body = aioclient_mock.mock_calls[-1][2]
    assert body["stream"] is True


async def test_working_phrase_before_tool(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A tool before any text: the working phrase is said, the tool is logged as external."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(
        CHAT,
        text=stream(
            tool("c1", "running"),
            tool("c1", "completed"),
            chunk("Seventy two and sunny."),
            chunk(finish="stop"),
        ),
        headers=SSE,
    )
    result = await _say(hass)
    assert _speech(result) == "Seventy two and sunny."  # the final message
    assert _assistant_text(hass, result) == f"{DEFAULT_WORKING_PHRASE} Seventy two and sunny."
    log = _log(hass, result)
    calls = [c for c in log if isinstance(c, conversation.AssistantContent) and c.tool_calls]
    assert len(calls) == 1
    assert calls[0].content.strip() == DEFAULT_WORKING_PHRASE
    assert calls[0].tool_calls[0].external is True
    assert calls[0].tool_calls[0].tool_name == "web_search"
    results = [c for c in log if isinstance(c, conversation.ToolResultContent)]
    assert [r.tool_call_id for r in results] == ["c1"]


async def test_no_working_phrase_when_hermes_spoke(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Hermes' own "let me check" replaces the working phrase; a partial sentence is flushed first."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(
        CHAT,
        text=stream(
            chunk("Let me check"),
            tool("c1", "running"),
            tool("c1", "completed"),
            tool("c2", "running"),
            tool("c2", "completed"),
            chunk("Done."),
            chunk(finish="stop"),
        ),
        headers=SSE,
    )
    result = await _say(hass)
    assert _assistant_text(hass, result) == "Let me check Done."
    assert DEFAULT_WORKING_PHRASE not in _assistant_text(hass, result)
    assert len([c for c in _log(hass, result) if isinstance(c, conversation.ToolResultContent)]) == 2


async def test_working_phrase_off(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """An empty working phrase says nothing extra."""
    await _setup(hass, aioclient_mock, {CONF_WORKING_PHRASE: ""})
    aioclient_mock.post(
        CHAT,
        text=stream(tool("c1", "running"), tool("c1", "completed"), chunk("Hi."), chunk(finish="stop")),
        headers=SSE,
    )
    assert _assistant_text(hass, await _say(hass)) == "Hi."


STREAM_ERRORS = {
    "failed_run": stream(chunk(finish="error", hermes={"failed": True, "error": "boom"})),
    "rate_limited": stream(chunk(finish="error", error={"message": "HTTP 429 usage limit"})),
    "length_without_text": stream(chunk(finish="length")),
    "no_text": stream(chunk(finish="stop")),
    "tool_then_nothing": stream(tool("c1", "running"), tool("c1", "completed"), chunk(finish="stop")),
    "provider_auth_text": stream(
        chunk("Provider authentication failed: No Codex credentials stored. Run hermes auth add boom"),
        chunk(finish="stop"),
    ),
}


@pytest.mark.parametrize("case", list(STREAM_ERRORS))
async def test_stream_errors_speak_fallback(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed or empty stream ends with the fallback message."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(CHAT, text=STREAM_ERRORS[case], headers=SSE)
    result = await _say(hass)
    assert _speech(result) == DEFAULT_FALLBACK_MESSAGE
    assert "boom" not in _assistant_text(hass, result)


async def test_error_after_text_keeps_text(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """What was already said stays; the fallback follows."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(
        CHAT,
        text=stream(chunk("Checking now. "), chunk(finish="error", hermes={"failed": True})),
        headers=SSE,
    )
    assert _assistant_text(hass, await _say(hass)) == f"Checking now. {DEFAULT_FALLBACK_MESSAGE}"


async def test_stream_option_off_uses_json(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """With streaming off the request asks for one JSON reply."""
    await _setup(hass, aioclient_mock, {CONF_STREAM: False})
    aioclient_mock.post(CHAT, json=reply("Plain."))
    assert _speech(await _say(hass)) == "Plain."
    assert aioclient_mock.mock_calls[-1][2]["stream"] is False


async def test_cleanup_off_passes_raw(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """With cleanup off, streamed text is passed on as is."""
    await _setup(hass, aioclient_mock, {CONF_VOICE_CLEANUP: False})
    aioclient_mock.post(CHAT, text=stream(chunk("**a**"), chunk(" b"), chunk(finish="stop")), headers=SSE)
    assert _speech(await _say(hass)) == "**a** b"


def test_chunker_sentences_and_code() -> None:
    """Sentences come out whole; an open code fence is held back and then dropped."""
    c = VoiceChunker(cleanup=True)
    assert c.feed("Version 3.5 is") == ""
    assert c.feed(" out. Next") == "Version 3.5 is out."
    assert c.feed(" run ```ls -la. now") == ""
    assert c.feed(" ok``` then. ") == "Next run then."
    assert c.flush() == ""
    assert c.feed("## Steps\n\n- one\n- two") == "Steps."
    assert c.flush() == "one. two"


async def test_sentences_end_with_blank_line(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Each spoken sentence ends with a blank line, so Piper speaks it at once instead of waiting for the next."""
    await _setup(hass, aioclient_mock)
    aioclient_mock.post(CHAT, text=stream(chunk("One. "), chunk("Two."), chunk(finish="stop")), headers=SSE)
    result = await _say(hass)
    texts = [
        c.content for c in _log(hass, result) if isinstance(c, conversation.AssistantContent) and c.content
    ]
    assert "".join(texts) == "One.\n\nTwo.\n\n"
