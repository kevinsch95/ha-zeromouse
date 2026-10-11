"""Sensors for ZeroMOUSE."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import CLASSIFICATIONS, ENTRY_MODES, EXIT_MODES
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator, ZeroMouseDevice
from .entity import ZeroMouseEntity, async_setup_device_entities

PARALLEL_UPDATES = 0


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _entry_mode(device: ZeroMouseDevice) -> str | None:
    return ENTRY_MODES.get(_int_or_none(device.system.get("kkzmBlockModeIn")))


def _exit_mode(device: ZeroMouseDevice) -> str | None:
    return EXIT_MODES.get(_int_or_none(device.system.get("kkzmBlockModeOut")))


def _last_event_attrs(device: ZeroMouseDevice) -> Mapping[str, Any] | None:
    event = device.latest_event
    if event is None:
        return None
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "classification": event.raw_classification,
        "cat": device.cat_name(event.cat_cluster_id),
        "image_count": len(event.image_indexes),
        "favorite": event.is_favorite,
    }


def _exit_mode_attrs(device: ZeroMouseDevice) -> Mapping[str, Any] | None:
    system = device.system
    if _exit_mode(device) != "time_based":
        return None
    return {
        "open_at": system.get("kkzmOutOpenAt"),
        "close_at": system.get("kkzmOutCloseAt"),
    }


@dataclass(frozen=True, kw_only=True)
class ZeroMouseSensorDescription(SensorEntityDescription):
    """Describes a ZeroMOUSE sensor."""

    value_fn: Callable[[ZeroMouseDevice], StateType | datetime]
    attrs_fn: Callable[[ZeroMouseDevice], Mapping[str, Any] | None] = lambda _: None


SENSORS: tuple[ZeroMouseSensorDescription, ...] = (
    ZeroMouseSensorDescription(
        key="last_event",
        translation_key="last_event",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.latest_event.time if d.latest_event else None,
        attrs_fn=_last_event_attrs,
    ),
    ZeroMouseSensorDescription(
        key="last_classification",
        translation_key="last_classification",
        device_class=SensorDeviceClass.ENUM,
        options=CLASSIFICATIONS,
        value_fn=lambda d: d.latest_event.classification if d.latest_event else None,
    ),
    ZeroMouseSensorDescription(
        key="last_cat",
        translation_key="last_cat",
        value_fn=lambda d: (
            d.cat_name(d.latest_event.cat_cluster_id) if d.latest_event else None
        ),
    ),
    ZeroMouseSensorDescription(
        key="event_count",
        translation_key="event_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d: _int_or_none(d.flap.get("eventCount")),
    ),
    ZeroMouseSensorDescription(
        key="entry_mode",
        translation_key="entry_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(ENTRY_MODES.values()),
        value_fn=_entry_mode,
    ),
    ZeroMouseSensorDescription(
        key="exit_mode",
        translation_key="exit_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(EXIT_MODES.values()),
        value_fn=_exit_mode,
        attrs_fn=_exit_mode_attrs,
    ),
    ZeroMouseSensorDescription(
        key="boot_count",
        translation_key="boot_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _int_or_none(d.flap.get("bootCount")),
    ),
    ZeroMouseSensorDescription(
        key="network",
        translation_key="network",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.flap.get("networkName"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up ZeroMOUSE sensors."""

    def _factory(coordinator: ZeroMouseCoordinator, device_id: str):
        return (
            ZeroMouseSensor(coordinator, device_id, description)
            for description in SENSORS
        )

    async_setup_device_entities(entry, async_add_entities, _factory)


class ZeroMouseSensor(ZeroMouseEntity, SensorEntity):
    """A ZeroMOUSE sensor."""

    entity_description: ZeroMouseSensorDescription

    def __init__(
        self,
        coordinator: ZeroMouseCoordinator,
        device_id: str,
        description: ZeroMouseSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the state."""
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return extra attributes."""
        return self.entity_description.attrs_fn(self.device)
