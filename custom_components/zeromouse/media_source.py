"""ZeroMOUSE event history in the Home Assistant media browser.

Tree: ZeroMOUSE → device → day → event (animated GIF).
"""

from __future__ import annotations

from datetime import date
import logging

from aiohttp.web import HTTPNotFound, Request, Response

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import BrowseError, MediaClass
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .api import ZeroMouseError
from .const import CLASSIFICATION_LABELS, DOMAIN
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator, ZeroMouseEvent
from .history import HISTORY_DAYS, local_day

_LOGGER = logging.getLogger(__name__)

GIF_MIME = "image/gif"
VIEW_URL = "/api/zeromouse/media/{device_id}/{event_id}/{kind}"


def _media_url(device_id: str, event_id: str, kind: str) -> str:
    return VIEW_URL.format(device_id=device_id, event_id=event_id, kind=kind)


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up the ZeroMOUSE media source."""
    hass.http.register_view(ZeroMouseMediaView(hass))
    return ZeroMouseMediaSource(hass)


@callback
def _find_device(hass: HomeAssistant, device_id: str) -> ZeroMouseCoordinator | None:
    """Return the coordinator of the loaded entry that owns a device."""
    entries: list[ZeroMouseConfigEntry] = hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    for entry in entries:
        coordinator = entry.runtime_data
        if coordinator.data and device_id in coordinator.data:
            return coordinator
    return None


def _event_title(
    coordinator: ZeroMouseCoordinator, device_id: str, event: ZeroMouseEvent
) -> str:
    parts = [dt_util.as_local(event.time).strftime("%H:%M") if event.time else "--:--"]
    parts.append(CLASSIFICATION_LABELS.get(event.classification, event.classification))
    if cat := coordinator.data[device_id].cat_name(event.cat_cluster_id):
        parts.append(cat)
    return " · ".join(parts)


def _day_title(day: str, count: int) -> str:
    try:
        label = date.fromisoformat(day).strftime("%a %d %b %Y")
    except ValueError:
        label = day
    return f"{label} ({count})"


class ZeroMouseMediaSource(MediaSource):
    """Browse past ZeroMOUSE events as GIFs."""

    name = "ZeroMOUSE"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve an event to its GIF URL."""
        parts = (item.identifier or "").split("/")
        if len(parts) != 3 or _find_device(self.hass, parts[0]) is None:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="media_not_found",
                translation_placeholders={"identifier": item.identifier or ""},
            )
        device_id, _, event_id = parts
        return PlayMedia(_media_url(device_id, event_id, "animation.gif"), GIF_MIME)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Return the requested level of the tree."""
        parts = [p for p in (item.identifier or "").split("/") if p]
        if not parts:
            return self._browse_root()
        coordinator = _find_device(self.hass, parts[0])
        if coordinator is None:
            raise BrowseError(
                translation_domain=DOMAIN,
                translation_key="media_not_found",
                translation_placeholders={"identifier": item.identifier or ""},
            )
        try:
            events = await coordinator.history.async_get_events(parts[0])
        except ZeroMouseError as err:
            raise BrowseError(
                translation_domain=DOMAIN,
                translation_key="history_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if len(parts) == 1:
            return self._browse_device(coordinator, parts[0], events)
        return self._browse_day(coordinator, parts[0], parts[1], events)

    def _browse_root(self) -> BrowseMediaSource:
        children = []
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator: ZeroMouseCoordinator = entry.runtime_data
            for device_id, device in (coordinator.data or {}).items():
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=device_id,
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="",
                        title=device.name,
                        can_play=False,
                        can_expand=True,
                    )
                )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=self.name,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_device(
        self,
        coordinator: ZeroMouseCoordinator,
        device_id: str,
        events: list[ZeroMouseEvent],
    ) -> BrowseMediaSource:
        days: dict[str, list[ZeroMouseEvent]] = {}
        for event in events:  # newest first, so days come out newest first
            days.setdefault(local_day(event.time), []).append(event)
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{device_id}/{day}",
                media_class=MediaClass.DIRECTORY,
                media_content_type="",
                title=_day_title(day, len(day_events)),
                can_play=False,
                can_expand=True,
                thumbnail=_media_url(device_id, day_events[0].event_id, "thumbnail"),
            )
            for day, day_events in days.items()
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=device_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=f"{coordinator.data[device_id].name} – last {HISTORY_DAYS} days",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_day(
        self,
        coordinator: ZeroMouseCoordinator,
        device_id: str,
        day: str,
        events: list[ZeroMouseEvent],
    ) -> BrowseMediaSource:
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{device_id}/{day}/{event.event_id}",
                media_class=MediaClass.IMAGE,
                media_content_type=GIF_MIME,
                title=_event_title(coordinator, device_id, event),
                can_play=True,
                can_expand=False,
                thumbnail=_media_url(device_id, event.event_id, "thumbnail"),
            )
            for event in events
            if local_day(event.time) == day
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{device_id}/{day}",
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=_day_title(day, len(children)),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.IMAGE,
        )


class ZeroMouseMediaView(HomeAssistantView):
    """Serve event thumbnails and GIFs (authenticated)."""

    url = VIEW_URL
    name = "api:zeromouse:media"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass

    async def get(
        self, request: Request, device_id: str, event_id: str, kind: str
    ) -> Response:
        """Return a thumbnail (JPEG) or animation (GIF)."""
        if kind not in ("thumbnail", "animation.gif"):
            raise HTTPNotFound
        coordinator = _find_device(self.hass, device_id)
        if coordinator is None:
            raise HTTPNotFound
        try:
            event = await coordinator.history.async_get_event(device_id, event_id)
            if event is None:
                raise HTTPNotFound
            if kind == "thumbnail":
                body = await coordinator.history.async_get_thumbnail(device_id, event)
                content_type = "image/jpeg"
            else:
                body = await coordinator.history.async_get_gif(device_id, event)
                content_type = GIF_MIME
        except (ZeroMouseError, OSError, ValueError) as err:
            _LOGGER.debug("Could not serve %s for %s: %s", kind, event_id, err)
            raise HTTPNotFound from err
        if body is None:
            raise HTTPNotFound
        return Response(
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "private, max-age=86400"},
        )
