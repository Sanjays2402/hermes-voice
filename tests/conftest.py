"""Shared fixtures."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_voice.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DISPLAY_NAME,
    CONF_MODEL,
    DEFAULT_OPTIONS,
    DOMAIN,
)

BASE = "http://hermes.test:8650/v1"
KEY = "test-key-0123456789abcdef"
MODELS = {"object": "list", "data": [{"id": "voice-spike", "object": "model"}]}


@pytest.fixture(autouse=True)
async def auto_setup(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Load custom_components/ and the core integration conversation depends on."""
    assert await async_setup_component(hass, "homeassistant", {})


def reply(text: str = "Hello, captain. Over and out.", **extra: Any) -> dict[str, Any]:
    """An OpenAI chat.completion body as Hermes returns it."""
    body: dict[str, Any] = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "model": "voice-spike",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
    }
    body.update(extra)
    return body


@pytest.fixture
def mock_models(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """GET /v1/models succeeds."""
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    return aioclient_mock


def make_entry(options: dict[str, Any] | None = None) -> MockConfigEntry:
    """A configured entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Voice Spike",
        unique_id=BASE,
        data={
            CONF_BASE_URL: BASE,
            CONF_API_KEY: KEY,
            CONF_DISPLAY_NAME: "Voice Spike",
            CONF_MODEL: "voice-spike",
        },
        options={**DEFAULT_OPTIONS, **(options or {})},
    )
