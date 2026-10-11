"""Data update coordinator for ZeroMOUSE."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ZeroMouseApi, ZeroMouseApiError, ZeroMouseAuthError, ZeroMouseError
from .const import (
    CLASSIFICATION_UNKNOWN,
    CLASSIFICATIONS,
    DOMAIN,
    EVENT_NEW_DETECTION,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type ZeroMouseConfigEntry = ConfigEntry[ZeroMouseCoordinator]


def _truthy(value: Any) -> bool:
    """Interpret the backend's mixed bool/int flags."""
    return value not in (None, False, 0, "0", "false")


def parse_event_time(event: dict[str, Any]) -> datetime | None:
    """Parse the event time (epoch seconds/ms or ISO string)."""
    for key in ("eventTime", "createdAt"):
        value = event.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)) or (
            isinstance(value, str) and value.replace(".", "", 1).isdigit()
        ):
            number = float(value)
            if number > 1e12:  # milliseconds
                number /= 1000
            return datetime.fromtimestamp(number, UTC)
        if parsed := dt_util.parse_datetime(str(value)):
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


@dataclass
class ZeroMouseEvent:
    """A detection event."""

    event_id: str
    device_id: str
    time: datetime | None
    classification: str
    raw_classification: str | None
    event_type: str | None
    cat_cluster_id: str | None
    title_image_index: int
    image_indexes: list[int]
    is_favorite: bool
    raw: dict[str, Any]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ZeroMouseEvent:
        """Build from a GraphQL item."""
        raw_class = data.get("classification_byNet")
        classification = (
            raw_class if raw_class in CLASSIFICATIONS else CLASSIFICATION_UNKNOWN
        )
        indexes = sorted(
            {
                int(img["imageIndex"])
                for img in data.get("images") or []
                if img and img.get("imageIndex") is not None
            }
        )
        title = data.get("titleImageIndex")
        if title is None:
            title = indexes[0] if indexes else 0
        return cls(
            event_id=data["eventID"],
            device_id=data.get("deviceID", ""),
            time=parse_event_time(data),
            classification=classification,
            raw_classification=raw_class,
            event_type=data.get("type"),
            cat_cluster_id=data.get("catClusterId_byUser") or data.get("catClusterId"),
            title_image_index=int(title),
            image_indexes=indexes,
            is_favorite=_truthy(data.get("isFavorite")),
            raw=data,
        )


@dataclass
class ZeroMouseDevice:
    """State of one ZeroMOUSE device."""

    device_id: str
    flap: dict[str, Any]
    latest_event: ZeroMouseEvent | None = None
    reported: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Return the user-given name."""
        return self.flap.get("name") or f"ZeroMOUSE {self.device_id[-4:]}"

    @property
    def sw_version(self) -> str | None:
        """Return the firmware version."""
        parts = [self.flap.get(k) for k in ("verMajor", "verMinor", "verRevision")]
        if any(p is None for p in parts):
            return None
        return ".".join(str(p) for p in parts)

    def cat_name(self, cluster_id: str | None) -> str | None:
        """Resolve a cat cluster id to the name the user gave the cat."""
        if not cluster_id:
            return None
        for cluster in self.flap.get("catClusters") or []:
            if cluster and cluster.get("id") == cluster_id:
                return cluster.get("name") or cluster_id
        return cluster_id

    @property
    def system(self) -> dict[str, Any]:
        """Return the reported `system` section of the shadow."""
        value = self.reported.get("system")
        return value if isinstance(value, dict) else {}


class ZeroMouseCoordinator(DataUpdateCoordinator[dict[str, ZeroMouseDevice]]):
    """Polls the ZeroMOUSE cloud for all devices of the account."""

    config_entry: ZeroMouseConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ZeroMouseConfigEntry, api: ZeroMouseApi
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api

    async def _async_update_device(self, flap: dict[str, Any]) -> ZeroMouseDevice:
        device_id = flap["deviceID"]
        previous = (self.data or {}).get(device_id)
        device = ZeroMouseDevice(device_id=device_id, flap=flap)

        events = await self.api.async_list_events(device_id)
        visible = [
            e
            for e in events
            if e.get("eventID")
            and not _truthy(e.get("isDeleted"))
            and not _truthy(e.get("isHidden"))
        ]
        if visible:
            device.latest_event = ZeroMouseEvent.from_api(visible[0])
        elif previous:
            device.latest_event = previous.latest_event

        try:
            shadow = await self.api.async_get_shadow(device_id)
            reported = shadow.get("reported")
            device.reported = reported if isinstance(reported, dict) else {}
        except ZeroMouseError as err:
            # The shadow is optional; keep the last known state.
            _LOGGER.debug("Could not read shadow for %s: %s", device_id, err)
            if previous:
                device.reported = previous.reported
        return device

    async def _async_update_data(self) -> dict[str, ZeroMouseDevice]:
        try:
            flaps = await self.api.async_list_flaps()
            devices = await asyncio.gather(
                *(
                    self._async_update_device(flap)
                    for flap in flaps
                    if flap.get("deviceID")
                )
            )
        except ZeroMouseAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except ZeroMouseApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        data = {device.device_id: device for device in devices}
        self._fire_new_events(data)
        return data

    def _fire_new_events(self, data: dict[str, ZeroMouseDevice]) -> None:
        """Fire a bus event for detections that appeared since the last poll."""
        if self.data is None:  # first refresh: don't replay history
            return
        for device_id, device in data.items():
            event = device.latest_event
            if event is None:
                continue
            previous = self.data.get(device_id)
            previous_id = (
                previous.latest_event.event_id
                if previous and previous.latest_event
                else None
            )
            if event.event_id == previous_id:
                continue
            self.hass.bus.async_fire(
                EVENT_NEW_DETECTION,
                {
                    "device_id": device_id,
                    "device_name": device.name,
                    "event_id": event.event_id,
                    "classification": event.classification,
                    "type": event.event_type,
                    "cat": device.cat_name(event.cat_cluster_id),
                    "time": event.time.isoformat() if event.time else None,
                },
            )
