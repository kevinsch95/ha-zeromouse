"""Diagnostics for ZeroMOUSE."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import ZeroMouseConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "ownerID", "networkName", "identity"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ZeroMouseConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    devices = {
        device_id: {
            "flap": device.flap,
            "reported": device.reported,
            "latest_event": (
                asdict(device.latest_event) if device.latest_event else None
            ),
        }
        for device_id, device in (coordinator.data or {}).items()
    }
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "devices": async_redact_data(devices, TO_REDACT),
    }
