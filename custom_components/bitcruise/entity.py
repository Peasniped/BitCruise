"""Base entity for BitCruise."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BitCruiseCoordinator


class BitCruiseEntity(CoordinatorEntity[BitCruiseCoordinator]):
    """Common device and naming for every BitCruise entity.

    One logical device per config entry. It represents the planner, and
    deliberately does not claim ownership of the vehicle or charger devices,
    which belong to their own integrations.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: BitCruiseCoordinator, key: str) -> None:
        """Bind the entity to its coordinator and device."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="BitCruise",
            model="Charge planner",
            entry_type=DeviceEntryType.SERVICE,
            # Shown on the device page, so which build is running is answerable
            # without opening the integration list or reading the manifest.
            sw_version=coordinator.version,
        )
