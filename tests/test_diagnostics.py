"""Diagnostics redact secrets."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_voice.diagnostics import async_get_config_entry_diagnostics

from .conftest import BASE, KEY, MODELS, make_entry


async def test_diagnostics_redacts_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """API key and user key never appear in diagnostics."""
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    entry = make_entry({"user_key": "secret-user"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["data"]["api_key"] == "**REDACTED**"
    assert diag["options"]["user_key"] == "**REDACTED**"
    assert diag["model"] == "voice-spike"
    assert KEY not in str(diag) and "secret-user" not in str(diag)
