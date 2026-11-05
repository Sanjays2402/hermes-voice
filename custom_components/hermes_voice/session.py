"""Map Home Assistant conversations/devices to Hermes session ids."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    MAX_HEADER_LEN,
    SESSION_PER_DEVICE,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)

_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
_SAVE_DELAY = 10


def _safe(part: str) -> str:
    """Hermes uses session ids in file names: keep them to a conservative charset."""
    return _UNSAFE.sub("_", part)


def conversation_session_id(conversation_id: str) -> str:
    """Session id for per_conversation mode."""
    return f"ha-{_safe(conversation_id)}"[:MAX_HEADER_LEN]


class SessionResolver:
    """Resolve the Hermes session id for a turn.

    per_conversation: "ha-<conversation_id>". HA itself starts a new conversation id after
    5 minutes of silence, so this follows HA's own idea of a conversation.

    per_device: "ha-dev-<device>-<window>". A window starts at the first turn and stays open while
    turns keep arriving less than ``idle_reset_minutes`` apart. Window state survives restarts
    (HA storage helper). Turns without a device (typed text, REST calls) use the HA user id,
    then the conversation id, as the "device".
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
    ) -> None:
        """Initialize the resolver."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}.{entry_id}")
        self._windows: dict[str, dict[str, float]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load window state."""
        data = await self._store.async_load()
        self._windows = dict((data or {}).get("windows", {}))
        self._loaded = True

    @property
    def windows(self) -> dict[str, dict[str, float]]:
        """Current window state (for diagnostics)."""
        return self._windows

    @staticmethod
    def device_key(device_id: str | None, user_id: str | None, conversation_id: str) -> str:
        """Pick the stable key a per_device window hangs off."""
        if device_id:
            return _safe(device_id)
        if user_id:
            return f"user-{_safe(user_id)}"
        return f"conv-{_safe(conversation_id)}"

    async def async_resolve(
        self,
        *,
        mode: str,
        conversation_id: str,
        device_id: str | None,
        user_id: str | None,
        idle_reset_minutes: float,
    ) -> str:
        """Return the session id for this turn and record the activity."""
        if mode != SESSION_PER_DEVICE:
            return conversation_session_id(conversation_id)
        if not self._loaded:
            await self.async_load()
        key = self.device_key(device_id, user_id, conversation_id)
        now = dt_util.utcnow().timestamp()
        window = self._windows.get(key)
        if window is None or now - window["last"] > idle_reset_minutes * 60:
            window = {"start": now, "last": now}
        else:
            window = {"start": window["start"], "last": now}
        self._windows[key] = window
        self._store.async_delay_save(lambda: {"windows": self._windows}, _SAVE_DELAY)
        return f"ha-dev-{key}-{int(window['start'])}"[:MAX_HEADER_LEN]
