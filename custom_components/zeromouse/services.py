"""Actions for the ZeroMOUSE integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import ZeroMouseError
from .const import DOMAIN, IMAGE_SUBFOLDER, MAX_IMAGES_PER_EVENT
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator
from .gif import build_gif

_LOGGER = logging.getLogger(__name__)

SERVICE_DOWNLOAD_IMAGES = "download_images"
ATTR_DEVICE_ID = "device_id"
ATTR_EVENT_ID = "event_id"
ATTR_DIRECTORY = "directory"

DOWNLOAD_IMAGES_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_EVENT_ID): cv.string,
        vol.Optional(ATTR_DIRECTORY): cv.string,
    }
)


def _resolve_device(
    hass: HomeAssistant, ha_device_id: str
) -> tuple[ZeroMouseCoordinator, str]:
    """Map a Home Assistant device id to (coordinator, ZeroMOUSE device id)."""
    device = dr.async_get(hass).async_get(ha_device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="device_not_found"
        )
    zm_id = next((ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None)
    for entry_id in device.config_entries:
        entry: ZeroMouseConfigEntry | None = hass.config_entries.async_get_entry(
            entry_id
        )
        if (
            entry
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
            and zm_id
        ):
            return entry.runtime_data, zm_id
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="device_not_found"
    )


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _async_download_images(call: ServiceCall) -> ServiceResponse:
    """Download all images of an event (default: the latest) to a folder."""
    hass = call.hass
    coordinator, zm_id = _resolve_device(hass, call.data[ATTR_DEVICE_ID])
    directory = call.data.get(ATTR_DIRECTORY) or str(
        Path(hass.config.media_dirs.get("local") or hass.config.path("media"))
        / IMAGE_SUBFOLDER
    )
    if not hass.config.is_allowed_path(directory):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="directory_not_allowed",
            translation_placeholders={"directory": directory},
        )

    device = coordinator.data.get(zm_id)
    latest = device.latest_event if device else None
    event_id = call.data.get(ATTR_EVENT_ID) or (latest.event_id if latest else None)
    if not event_id:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_event"
        )

    # Known image indexes for the latest event; otherwise probe sequentially.
    is_latest = latest is not None and latest.event_id == event_id
    known = latest.image_indexes if is_latest else []
    capture_times = (
        dict(zip(known, latest.image_times, strict=True)) if is_latest else {}
    )
    candidates = known or list(range(MAX_IMAGES_PER_EVENT))

    files: list[str] = []
    frames: list[bytes] = []
    times: list[int | None] = []
    misses = 0
    for index in candidates:
        try:
            image = await coordinator.api.async_get_image(zm_id, event_id, index)
        except ZeroMouseError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="download_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if image is None:
            misses += 1
            # Probing unknown indexes: stop after a gap once images were found,
            # or give up if the first few indexes don't exist.
            if not known and misses >= (2 if files else 3):
                break
            continue
        misses = 0
        path = Path(directory) / zm_id / event_id / f"{index}.jpg"
        await hass.async_add_executor_job(_write_file, path, image)
        files.append(str(path))
        frames.append(image)
        times.append(capture_times.get(index))

    if not files:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="no_images",
            translation_placeholders={"event_id": event_id},
        )
    response: dict[str, Any] = {"event_id": event_id, "files": files}
    if len(frames) > 1:
        try:
            gif = await hass.async_add_executor_job(build_gif, frames, times)
        except (OSError, ValueError) as err:
            # The photos are saved; only the animation is skipped.
            _LOGGER.warning("Could not build animation for %s: %s", event_id, err)
        else:
            gif_path = Path(directory) / zm_id / event_id / "animation.gif"
            await hass.async_add_executor_job(_write_file, gif_path, gif)
            response["animation"] = str(gif_path)
    return response


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_IMAGES,
        _async_download_images,
        schema=DOWNLOAD_IMAGES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
