"""The ZeroMOUSE integration."""

from __future__ import annotations

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import ZeroMouseApi
from .const import DOMAIN
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator
from .realtime import ZeroMouseRealtime
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.SENSOR,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the ZeroMOUSE actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ZeroMouseConfigEntry) -> bool:
    """Set up ZeroMOUSE from a config entry."""
    session = async_get_clientsession(hass)
    api = ZeroMouseApi(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
    )
    coordinator = ZeroMouseCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Live events; cancelled automatically when the entry unloads.
    realtime = ZeroMouseRealtime(
        session,
        api.async_get_access_token,
        coordinator.async_handle_live_event,
        coordinator.async_set_live_connected,
    )
    entry.async_create_background_task(
        hass, realtime.async_run(), f"{DOMAIN} live events"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZeroMouseConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
