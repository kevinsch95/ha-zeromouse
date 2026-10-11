"""Event history, thumbnails and GIFs for the media browser."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta
import io
import logging
import time
from typing import TYPE_CHECKING

from PIL import Image

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import ZeroMouseApi
from .const import MAX_IMAGES_PER_EVENT
from .gif import build_gif

if TYPE_CHECKING:
    from .coordinator import ZeroMouseEvent

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS = 30
HISTORY_PAGE_SIZE = 100
HISTORY_MAX_PAGES = 20  # at most 2000 events
HISTORY_CACHE_SECONDS = 120
THUMBNAIL_WIDTH = 320
MAX_CACHED_GIFS = 20
MAX_CACHED_THUMBNAILS = 300


class _LRU[K, V]:
    """Tiny LRU cache."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._data: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: K, value: V) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._size:
            self._data.popitem(last=False)


def _make_thumbnail(data: bytes) -> bytes:
    """Return a small JPEG (blocking)."""
    with Image.open(io.BytesIO(data)) as img:
        frame = img.convert("RGB")
    if frame.width > THUMBNAIL_WIDTH:
        height = round(frame.height * THUMBNAIL_WIDTH / frame.width)
        frame = frame.resize((THUMBNAIL_WIDTH, height), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    frame.save(out, format="JPEG", quality=80)
    return out.getvalue()


async def async_fetch_frames(
    api: ZeroMouseApi,
    device_id: str,
    event_id: str,
    indexes: list[int],
    times: list[int | None],
) -> tuple[list[tuple[int, bytes]], list[int | None]]:
    """Download an event's photos.

    With known indexes, fetch exactly those. Without (older events seen only
    by id), probe 0, 1, 2, ... until a gap after the last photo.
    """
    capture = dict(zip(indexes, times, strict=True))
    candidates = indexes or list(range(MAX_IMAGES_PER_EVENT))
    frames: list[tuple[int, bytes]] = []
    frame_times: list[int | None] = []
    misses = 0
    for index in candidates:
        data = await api.async_get_image(device_id, event_id, index)
        if data is None:
            misses += 1
            if not indexes and misses >= (2 if frames else 3):
                break
            continue
        misses = 0
        frames.append((index, data))
        frame_times.append(capture.get(index))
    return frames, frame_times


class ZeroMouseHistory:
    """Read-through cache of past events, per device."""

    def __init__(self, hass: HomeAssistant, api: ZeroMouseApi) -> None:
        """Initialize."""
        self._hass = hass
        self._api = api
        self._events: dict[str, tuple[float, list[ZeroMouseEvent]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._gifs: _LRU[str, bytes] = _LRU(MAX_CACHED_GIFS)
        self._thumbs: _LRU[str, bytes] = _LRU(MAX_CACHED_THUMBNAILS)

    async def async_get_events(
        self, device_id: str, *, force: bool = False
    ) -> list[ZeroMouseEvent]:
        """Return visible events of the last HISTORY_DAYS days, newest first."""
        # Imported here to avoid a circular import with the coordinator.
        from .coordinator import ZeroMouseEvent, is_visible  # noqa: PLC0415

        lock = self._locks.setdefault(device_id, asyncio.Lock())
        async with lock:
            cached = self._events.get(device_id)
            if (
                cached
                and not force
                and time.monotonic() - cached[0] < HISTORY_CACHE_SECONDS
            ):
                return cached[1]

            cutoff = dt_util.utcnow() - timedelta(days=HISTORY_DAYS)
            events: list[ZeroMouseEvent] = []
            token: str | None = None
            for _ in range(HISTORY_MAX_PAGES):
                items, token = await self._api.async_list_events_page(
                    device_id, HISTORY_PAGE_SIZE, token
                )
                reached_cutoff = False
                for item in items:
                    if not is_visible(item):
                        continue
                    event = ZeroMouseEvent.from_api(item)
                    if event.time and event.time < cutoff:
                        reached_cutoff = True
                        break
                    events.append(event)
                if reached_cutoff or not token:
                    break
            self._events[device_id] = (time.monotonic(), events)
            return events

    async def async_get_event(
        self, device_id: str, event_id: str
    ) -> ZeroMouseEvent | None:
        """Look up one event, refreshing the list once if it's not cached."""
        for force in (False, True):
            for event in await self.async_get_events(device_id, force=force):
                if event.event_id == event_id:
                    return event
        return None

    async def async_get_gif(
        self, device_id: str, event: ZeroMouseEvent
    ) -> bytes | None:
        """Return the event's animation (cached)."""
        key = f"{device_id}/{event.event_id}"
        if (gif := self._gifs.get(key)) is not None:
            return gif
        frames, times = await async_fetch_frames(
            self._api, device_id, event.event_id, event.image_indexes, event.image_times
        )
        if not frames:
            return None
        gif = await self._hass.async_add_executor_job(
            build_gif, [data for _, data in frames], times
        )
        if not event.image_indexes or len(frames) == len(event.image_indexes):
            self._gifs.put(key, gif)
        return gif

    async def async_get_thumbnail(
        self, device_id: str, event: ZeroMouseEvent
    ) -> bytes | None:
        """Return a small JPEG of the event's title photo (cached)."""
        key = f"{device_id}/{event.event_id}"
        if (thumb := self._thumbs.get(key)) is not None:
            return thumb
        data = await self._api.async_get_image(
            device_id, event.event_id, event.title_image_index
        )
        if data is None:
            return None
        thumb = await self._hass.async_add_executor_job(_make_thumbnail, data)
        self._thumbs.put(key, thumb)
        return thumb


def local_day(when: datetime | None) -> str:
    """Return the local calendar day (YYYY-MM-DD) of a timestamp."""
    if when is None:
        return "unknown"
    return dt_util.as_local(when).date().isoformat()
