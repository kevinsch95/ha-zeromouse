"""Switches for ZeroMOUSE."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator
from .entity import ZeroMouseEntity, async_setup_device_entities

PARALLEL_UPDATES = 1

# Device shadow location of the app's "Block prey" switch (1 = on, 0 = off).
BLOCK_PREY_SECTION = "rfid"
BLOCK_PREY_KEY = "blockEnabled"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZeroMOUSE switches."""

    def _factory(coordinator: ZeroMouseCoordinator, device_id: str):
        return (ZeroMouseBlockPreySwitch(coordinator, device_id),)

    async_setup_device_entities(entry, async_add_entities, _factory)


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on")
    return value in (1, True)


class ZeroMouseBlockPreySwitch(ZeroMouseEntity, SwitchEntity):
    """The app's "Block prey" setting.

    On: ZeroMOUSE blocks the flap when it detects prey.
    Off: it only records events (and still sends notifications).
    """

    _attr_translation_key = "block_prey"

    def __init__(self, coordinator: ZeroMouseCoordinator, device_id: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id, "block_prey")

    @property
    def is_on(self) -> bool | None:
        """Return the requested state, like the app does."""
        requested, _ = self.device.shadow_value(BLOCK_PREY_SECTION, BLOCK_PREY_KEY)
        return _as_bool(requested)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any]:
        """Show whether the device has confirmed the setting yet."""
        requested, confirmed = self.device.shadow_value(
            BLOCK_PREY_SECTION, BLOCK_PREY_KEY
        )
        return {
            "confirmed_by_device": _as_bool(confirmed),
            "pending": _as_bool(requested) != _as_bool(confirmed),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Block the flap when prey is detected."""
        await self.coordinator.async_set_shadow_value(
            self._device_id, BLOCK_PREY_SECTION, BLOCK_PREY_KEY, 1
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Only record events; never block."""
        await self.coordinator.async_set_shadow_value(
            self._device_id, BLOCK_PREY_SECTION, BLOCK_PREY_KEY, 0
        )
