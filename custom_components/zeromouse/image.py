"""Image entity showing the latest ZeroMOUSE detection."""

from __future__ import annotations

import logging

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import ZeroMouseError
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator
from .entity import ZeroMouseEntity, async_setup_device_entities

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ZeroMOUSE image entities."""

    def _factory(coordinator: ZeroMouseCoordinator, device_id: str):
        return (ZeroMouseLatestImage(coordinator, device_id),)

    async_setup_device_entities(entry, async_add_entities, _factory)


class ZeroMouseLatestImage(ZeroMouseEntity, ImageEntity):
    """The title image of the most recent event."""

    _attr_translation_key = "latest_event_image"
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: ZeroMouseCoordinator, device_id: str) -> None:
        """Initialize the image entity."""
        ZeroMouseEntity.__init__(self, coordinator, device_id, "latest_event_image")
        ImageEntity.__init__(self, coordinator.hass)
        self._image_key: tuple[str, int] | None = None
        self._cached: bytes | None = None
        self._missing = False
        self._update_from_event()

    def _update_from_event(self) -> None:
        event = self.device.latest_event if self.available else None
        key = (event.event_id, event.title_image_index) if event else None
        if key != self._image_key:
            self._image_key = key
            self._cached = None
            self._missing = False
            self._attr_image_last_updated = event.time if event else None
        elif self._missing:
            # Live events arrive before the photo is uploaded: bump the
            # timestamp so the frontend asks for the image again.
            self._missing = False
            self._attr_image_last_updated = dt_util.utcnow()
        self._attr_extra_state_attributes = (
            {
                "event_id": event.event_id,
                "image_index": event.title_image_index,
                "image_indexes": event.image_indexes,
            }
            if event
            else {}
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_event()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the JPEG bytes of the latest event's title image."""
        if self._image_key is None:
            return None
        if self._cached is not None:
            return self._cached
        event_id, index = self._image_key
        try:
            image = await self.coordinator.api.async_get_image(
                self._device_id, event_id, index
            )
        except ZeroMouseError as err:
            _LOGGER.warning("Could not download image for event %s: %s", event_id, err)
            return None
        if self._image_key != (event_id, index):
            return image
        if image is None:
            self._missing = True
            return None
        self._cached = image
        return image
