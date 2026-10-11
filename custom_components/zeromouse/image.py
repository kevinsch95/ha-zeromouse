"""Image entities showing the latest ZeroMOUSE detection."""

from __future__ import annotations

from collections.abc import Hashable
import logging
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import ZeroMouseError
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator, ZeroMouseEvent
from .entity import ZeroMouseEntity, async_setup_device_entities
from .gif import build_gif

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# How often to re-request an image that wasn't (fully) uploaded yet.
MAX_RETRIES = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ZeroMOUSE image entities."""

    def _factory(coordinator: ZeroMouseCoordinator, device_id: str):
        return (
            ZeroMouseLatestImage(coordinator, device_id),
            ZeroMouseLatestAnimation(coordinator, device_id),
        )

    async_setup_device_entities(entry, async_add_entities, _factory)


class _ZeroMouseEventImage(ZeroMouseEntity, ImageEntity):
    """Base for images derived from the latest event, with caching and retry."""

    def __init__(
        self, coordinator: ZeroMouseCoordinator, device_id: str, key: str
    ) -> None:
        """Initialize the image entity."""
        ZeroMouseEntity.__init__(self, coordinator, device_id, key)
        ImageEntity.__init__(self, coordinator.hass)
        self._image_key: Hashable | None = None
        self._cached: bytes | None = None
        self._incomplete = False
        self._retries = 0
        self._update_from_event()

    def _key_for(self, event: ZeroMouseEvent) -> Hashable | None:
        """Return what identifies the image content for an event."""
        raise NotImplementedError

    def _attributes(self, event: ZeroMouseEvent) -> dict[str, Any]:
        raise NotImplementedError

    async def _async_fetch(
        self, event: ZeroMouseEvent, key: Hashable
    ) -> tuple[bytes | None, bool]:
        """Return (image, complete)."""
        raise NotImplementedError

    def _update_from_event(self) -> None:
        event = self.device.latest_event if self.available else None
        key = self._key_for(event) if event else None
        if key != self._image_key:
            self._image_key = key
            self._cached = None
            self._incomplete = False
            self._retries = 0
            self._attr_image_last_updated = event.time if event and key else None
        elif self._incomplete and self._retries < MAX_RETRIES:
            # Photos are uploaded after the event is created: bump the
            # timestamp so the frontend asks for the image again.
            self._incomplete = False
            self._retries += 1
            self._attr_image_last_updated = dt_util.utcnow()
        self._attr_extra_state_attributes = self._attributes(event) if event else {}

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update_from_event()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        """Return the image bytes."""
        key = self._image_key
        event = self.device.latest_event if self.available else None
        if key is None or event is None:
            return None
        if self._cached is not None:
            return self._cached
        try:
            image, complete = await self._async_fetch(event, key)
        except ZeroMouseError as err:
            _LOGGER.warning(
                "Could not download images for event %s: %s", event.event_id, err
            )
            return None
        if key != self._image_key:
            return image
        if complete and image is not None:
            self._cached = image
        else:
            self._incomplete = True
        return image


class ZeroMouseLatestImage(_ZeroMouseEventImage):
    """The title photo of the most recent event."""

    _attr_translation_key = "latest_event_image"
    _attr_content_type = "image/jpeg"

    def __init__(self, coordinator: ZeroMouseCoordinator, device_id: str) -> None:
        """Initialize the image entity."""
        super().__init__(coordinator, device_id, "latest_event_image")

    def _key_for(self, event: ZeroMouseEvent) -> Hashable | None:
        return (event.event_id, event.title_image_index)

    def _attributes(self, event: ZeroMouseEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "image_index": event.title_image_index,
            "image_indexes": event.image_indexes,
        }

    async def _async_fetch(
        self, event: ZeroMouseEvent, key: Hashable
    ) -> tuple[bytes | None, bool]:
        image = await self.coordinator.api.async_get_image(
            self._device_id, event.event_id, event.title_image_index
        )
        return image, image is not None


class ZeroMouseLatestAnimation(_ZeroMouseEventImage):
    """An animated GIF of all photos of the most recent event."""

    _attr_translation_key = "latest_event_animation"
    _attr_content_type = "image/gif"

    def __init__(self, coordinator: ZeroMouseCoordinator, device_id: str) -> None:
        """Initialize the image entity."""
        super().__init__(coordinator, device_id, "latest_event_animation")

    def _key_for(self, event: ZeroMouseEvent) -> Hashable | None:
        # The photo list arrives with the follow-up poll after a live event.
        if not event.image_indexes:
            return None
        return (event.event_id, tuple(event.image_indexes))

    def _attributes(self, event: ZeroMouseEvent) -> dict[str, Any]:
        return {"event_id": event.event_id, "frames": len(event.image_indexes)}

    async def _async_fetch(
        self, event: ZeroMouseEvent, key: Hashable
    ) -> tuple[bytes | None, bool]:
        frames: list[bytes] = []
        times: list[int | None] = []
        for index, ms in zip(event.image_indexes, event.image_times, strict=True):
            data = await self.coordinator.api.async_get_image(
                self._device_id, event.event_id, index
            )
            if data is not None:
                frames.append(data)
                times.append(ms)
        if not frames:
            return None, False
        try:
            gif = await self.hass.async_add_executor_job(build_gif, frames, times)
        except (OSError, ValueError) as err:
            _LOGGER.warning("Could not build animation for %s: %s", event.event_id, err)
            return None, False
        return gif, len(frames) == len(event.image_indexes)
