"""Binary sensors for ZeroMOUSE."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CLASSIFICATION_PREY
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator
from .entity import ZeroMouseEntity, async_setup_device_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZeroMOUSE binary sensors."""

    def _factory(coordinator: ZeroMouseCoordinator, device_id: str):
        return (ZeroMouseLastEventPreySensor(coordinator, device_id),)

    async_setup_device_entities(entry, async_add_entities, _factory)


class ZeroMouseLastEventPreySensor(ZeroMouseEntity, BinarySensorEntity):
    """On when the most recent event was classified as prey."""

    _attr_translation_key = "prey_detected"

    def __init__(self, coordinator: ZeroMouseCoordinator, device_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id, "prey_detected")

    @property
    def is_on(self) -> bool | None:
        """Return True if the last event was prey."""
        event = self.device.latest_event
        if event is None:
            return None
        return event.classification == CLASSIFICATION_PREY
