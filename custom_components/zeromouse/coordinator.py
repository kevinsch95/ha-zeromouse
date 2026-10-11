"""Data update coordinator for ZeroMOUSE."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ZeroMouseApi, ZeroMouseApiError, ZeroMouseAuthError, ZeroMouseError
from .const import (
    CLASSIFICATION_UNKNOWN,
    CLASSIFICATIONS,
    DOMAIN,
    EVENT_NEW_DETECTION,
    LIVE_FOLLOWUP_DELAY,
    UPDATE_INTERVAL,
    UPDATE_INTERVAL_LIVE,
)

_LOGGER = logging.getLogger(__name__)

LIVE_EVENT_GRACE = timedelta(minutes=10)

type ZeroMouseConfigEntry = ConfigEntry[ZeroMouseCoordinator]


def _truthy(value: Any) -> bool:
    """Interpret the backend's mixed bool/int flags."""
    return value not in (None, False, 0, "0", "false")


def _is_older(event: ZeroMouseEvent, than: ZeroMouseEvent | None) -> bool:
    """Return True if `event` is strictly older than `than`."""
    if than is None or event.event_id == than.event_id:
        return False
    if event.time is None or than.time is None:
        return False
    return event.time < than.time


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
    """Keeps ZeroMOUSE data current via live events plus polling."""

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
        self.live_connected = False
        self._followup_unsub: CALLBACK_TYPE | None = None

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
        if previous and (kept := previous.latest_event):
            device.latest_event = self._pick_latest(device.latest_event, kept, events)

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

    @staticmethod
    def _pick_latest(
        polled: ZeroMouseEvent | None,
        kept: ZeroMouseEvent,
        events: list[dict[str, Any]],
    ) -> ZeroMouseEvent | None:
        """Choose between the polled newest event and the one we already had.

        The previous event wins only if the poll hasn't seen it at all: either
        the poll returned nothing, or it is a recent live event that the poll
        started too early to include. Deleted or hidden events still appear in
        the polled list, so those are dropped.
        """
        if kept.event_id in {e.get("eventID") for e in events}:
            return polled
        if polled is None:
            return kept
        recent = (
            kept.time is not None and dt_util.utcnow() - kept.time < LIVE_EVENT_GRACE
        )
        if recent and _is_older(polled, kept):
            return kept
        return polled

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
            if event.event_id == previous_id or (
                previous and _is_older(event, previous.latest_event)
            ):
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

    # ------------------------------------------------------------------
    # Live updates
    # ------------------------------------------------------------------

    @callback
    def async_set_live_connected(self, connected: bool) -> None:
        """Track the live connection and adapt the polling interval."""
        if connected == self.live_connected:
            return
        self.live_connected = connected
        self.update_interval = UPDATE_INTERVAL_LIVE if connected else UPDATE_INTERVAL
        _LOGGER.debug("Live updates %s", "connected" if connected else "disconnected")
        if not connected and self.config_entry.state is ConfigEntryState.LOADED:
            # Catch up on anything missed while the link was down.
            self.hass.async_create_task(self.async_request_refresh())
        self.async_update_listeners()

    @callback
    def async_handle_live_event(self, item: dict[str, Any]) -> None:
        """Apply an event pushed by the AppSync subscription."""
        if self.data is None or not item.get("eventID"):
            return
        device_id = item.get("deviceID")
        device = self.data.get(device_id)
        if device is None:
            _LOGGER.debug("Live event for unknown device %s, refreshing", device_id)
            self._schedule_followup()
            return
        if _truthy(item.get("isDeleted")) or _truthy(item.get("isHidden")):
            return

        event = ZeroMouseEvent.from_api(item)
        if event.raw_classification is None or _is_older(event, device.latest_event):
            # Not classified yet (or out of order): let the follow-up poll decide.
            self._schedule_followup()
            return

        _LOGGER.debug("Live event %s on %s", event.event_id, device_id)
        new_data = {**self.data, device_id: replace(device, latest_event=event)}
        self._fire_new_events(new_data)
        self.async_set_updated_data(new_data)
        # Images and counters arrive a little later; fetch them then.
        self._schedule_followup()

    @callback
    def _schedule_followup(self) -> None:
        if self._followup_unsub:
            self._followup_unsub()

        @callback
        def _run(_now: datetime) -> None:
            self._followup_unsub = None
            self.hass.async_create_task(self.async_request_refresh())

        self._followup_unsub = async_call_later(self.hass, LIVE_FOLLOWUP_DELAY, _run)

    async def async_shutdown(self) -> None:
        """Cancel pending follow-ups."""
        if self._followup_unsub:
            self._followup_unsub()
            self._followup_unsub = None
        await super().async_shutdown()
