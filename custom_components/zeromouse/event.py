"""Event entity firing on each new ZeroMOUSE detection."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CLASSIFICATIONS
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator
from .entity import ZeroMouseEntity, async_setup_device_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ZeroMOUSE event entities."""

    def _factory(coordinator: ZeroMouseCoordinator, device_id: str):
        return (ZeroMouseDetectionEvent(coordinator, device_id),)

    async_setup_device_entities(entry, async_add_entities, _factory)


class ZeroMouseDetectionEvent(ZeroMouseEntity, EventEntity):
    """Triggers when a new detection event appears in the cloud."""

    _attr_translation_key = "detection"
    _attr_event_types = CLASSIFICATIONS

    def __init__(self, coordinator: ZeroMouseCoordinator, device_id: str) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator, device_id, "detection")
        event = self.device.latest_event
        # Remember the event that existed at startup so it isn't re-fired.
        self._last_event_id = event.event_id if event else None

    @callback
    def _handle_coordinator_update(self) -> None:
        if (
            self.available
            and (event := self.device.latest_event)
            and event.event_id != self._last_event_id
        ):
            self._last_event_id = event.event_id
            self._trigger_event(
                event.classification,
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "cat": self.device.cat_name(event.cat_cluster_id),
                    "time": event.time.isoformat() if event.time else None,
                },
            )
        super()._handle_coordinator_update()
