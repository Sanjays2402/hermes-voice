"""Config flow, options flow and reauth."""

from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hermes_voice.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_DISPLAY_NAME,
    CONF_IDLE_RESET_MINUTES,
    CONF_MODEL,
    CONF_SESSION_MODE,
    DEFAULT_OPTIONS,
    DOMAIN,
    SESSION_PER_DEVICE,
)

from .conftest import BASE, KEY, MODELS, make_entry


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_user_flow_success(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Valid URL + key creates an entry; /v1 is appended; no model call is made."""
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_BASE_URL: "http://hermes.test:8650/", CONF_API_KEY: KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "voice-spike"
    assert result["data"] == {
        CONF_BASE_URL: BASE,
        CONF_API_KEY: KEY,
        CONF_DISPLAY_NAME: "voice-spike",
        CONF_MODEL: "voice-spike",
    }
    assert result["options"] == DEFAULT_OPTIONS
    # Only GET /models was called (flow + entry setup), never a model call.
    assert {(c[0], str(c[1])) for c in aioclient_mock.mock_calls} == {("GET", f"{BASE}/models")}
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {KEY}"


async def test_user_flow_multiplexed_url_and_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A /p/<profile> route works and the display name is kept."""
    url = "http://hermes.test:8642/p/kitchen/v1"
    aioclient_mock.get(f"{url}/models", json={"data": [{"id": "kitchen"}]})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: "http://hermes.test:8642/p/kitchen", CONF_API_KEY: KEY, CONF_DISPLAY_NAME: "Kitchen"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Kitchen"
    assert result["data"][CONF_BASE_URL] == url


async def test_two_profiles_two_entries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """One entry per profile URL; the same URL twice aborts."""
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    aioclient_mock.get("http://hermes.test:8660/v1/models", json={"data": [{"id": "other"}]})
    for url in (BASE, "http://hermes.test:8660/v1"):
        r = await _start(hass)
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {CONF_BASE_URL: url, CONF_API_KEY: KEY}
        )
        assert r["type"] is FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY})
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "already_configured"


async def test_user_flow_invalid_auth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """401 -> invalid_auth."""
    aioclient_mock.get(f"{BASE}/models", status=401)
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: "bad"}
    )
    assert r["type"] is FlowResultType.FORM
    assert r["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Connection error -> cannot_connect; then recovers."""
    aioclient_mock.get(f"{BASE}/models", exc=aiohttp.ClientConnectionError())
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY})
    assert r["errors"] == {"base": "cannot_connect"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY})
    assert r["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_timeout_and_http_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Timeout and HTTP 500 -> cannot_connect."""
    aioclient_mock.get(f"{BASE}/models", exc=TimeoutError())
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY})
    assert r["errors"] == {"base": "cannot_connect"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/models", status=500)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY})
    assert r["errors"] == {"base": "cannot_connect"}


async def test_user_flow_no_models(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Empty model list -> no_models."""
    aioclient_mock.get(f"{BASE}/models", json={"data": []})
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY})
    assert r["errors"] == {"base": "no_models"}


async def test_user_flow_invalid_url(hass: HomeAssistant) -> None:
    """Non-http URL is rejected before any request."""
    r = await _start(hass)
    r = await hass.config_entries.flow.async_configure(
        r["flow_id"], {CONF_BASE_URL: "hermes.local", CONF_API_KEY: KEY}
    )
    assert r["errors"] == {CONF_BASE_URL: "invalid_url"}


async def test_options_flow(hass: HomeAssistant, mock_models: AiohttpClientMocker) -> None:
    """Options are saved and the entry reloads."""
    entry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.options.async_configure(
        r["flow_id"],
        {
            CONF_SESSION_MODE: SESSION_PER_DEVICE,
            CONF_IDLE_RESET_MINUTES: 0.1,
            "voice_cleanup": False,
            "fallback_message": "Brain offline.",
            "timeout": 12,
            "stream": False,
            "working_phrase": "Hang on.",
            "send_time": False,
            "screen_activity": "all",
            "avoid_name": False,
        },
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_SESSION_MODE] == SESSION_PER_DEVICE
    assert entry.options[CONF_IDLE_RESET_MINUTES] == 0.1
    assert entry.options["voice_cleanup"] is False
    assert entry.options["user_key"] == ""
    assert entry.options["stream"] is False
    assert entry.options["working_phrase"] == "Hang on."
    assert entry.options["send_time"] is False
    assert entry.options["screen_activity"] == "all"
    assert entry.options["avoid_name"] is False


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A rejected key at setup starts reauth; a new key fixes it."""
    aioclient_mock.get(f"{BASE}/models", status=401)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    r = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {CONF_API_KEY: "new-key-0123456789"}
    )
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new-key-0123456789"


async def test_unfinished_flow_does_not_block(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A first attempt left open after an error (wrong key) does not block a new attempt for the same URL."""
    aioclient_mock.get(f"{BASE}/models", status=401)
    first = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        first["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: "wrong"}
    )
    assert r["errors"] == {"base": "invalid_auth"}
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{BASE}/models", json=MODELS)
    second = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(
        second["flow_id"], {CONF_BASE_URL: BASE, CONF_API_KEY: KEY}
    )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert not hass.config_entries.flow.async_progress()  # the unfinished one was aborted
