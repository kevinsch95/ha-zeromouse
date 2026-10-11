"""Base entity for ZeroMOUSE."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZeroMouseConfigEntry, ZeroMouseCoordinator, ZeroMouseDevice


class ZeroMouseEntity(CoordinatorEntity[ZeroMouseCoordinator]):
    """Common base for all ZeroMOUSE entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ZeroMouseCoordinator, device_id: str, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        device = coordinator.data[device_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device.name,
            manufacturer=MANUFACTURER,
            model=device.flap.get("model"),
            sw_version=device.sw_version,
            hw_version=(
                str(device.flap["verHardware"])
                if device.flap.get("verHardware") is not None
                else None
            ),
            serial_number=device_id,
        )

    @property
    def device(self) -> ZeroMouseDevice:
        """Return the current device data."""
        return self.coordinator.data[self._device_id]

    @property
    def available(self) -> bool:
        """Return True if the device is still part of the account."""
        return super().available and self._device_id in self.coordinator.data


def async_setup_device_entities(
    entry: ZeroMouseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    factory: Callable[[ZeroMouseCoordinator, str], Iterable[Entity]],
) -> None:
    """Add entities for current devices and for devices added later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        new_ids = set(coordinator.data) - known
        if not new_ids:
            return
        known.update(new_ids)
        async_add_entities(
            entity
            for device_id in new_ids
            for entity in factory(coordinator, device_id)
        )

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))
