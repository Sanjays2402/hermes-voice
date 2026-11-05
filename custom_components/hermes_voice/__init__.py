"""Hermes Voice: use a Hermes Agent profile as a Home Assistant conversation agent."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import HermesAuthError, HermesClient, HermesError
from .const import CONF_API_KEY, CONF_BASE_URL, CONF_MODEL, DOMAIN
from .session import SessionResolver

PLATFORMS = (Platform.CONVERSATION,)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass(slots=True)
class HermesRuntimeData:
    """Per-entry runtime objects."""

    client: HermesClient
    model: str
    sessions: SessionResolver


type HermesConfigEntry = ConfigEntry[HermesRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Set up one Hermes profile."""
    client = HermesClient(async_get_clientsession(hass), entry.data[CONF_BASE_URL], entry.data[CONF_API_KEY])
    try:
        models = await client.async_list_models()
    except HermesAuthError as err:
        raise ConfigEntryAuthFailed("Hermes rejected the API key") from err
    except HermesError as err:
        raise ConfigEntryNotReady(f"Hermes gateway not reachable: {err}") from err

    model = entry.data.get(CONF_MODEL) or (models[0] if models else "hermes-agent")
    sessions = SessionResolver(hass, entry.entry_id)
    await sessions.async_load()
    entry.runtime_data = HermesRuntimeData(client=client, model=model, sessions=sessions)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HermesConfigEntry) -> bool:
    """Unload an entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
