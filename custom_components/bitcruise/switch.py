"""The master switch for BitCruise's planning."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BitCruiseConfigEntry, BitCruiseCoordinator
from .entity import BitCruiseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BitCruiseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BitCruise switch."""
    async_add_entities([BitCruiseSmartCharging(entry.runtime_data)])


class BitCruiseSmartCharging(BitCruiseEntity, SwitchEntity):
    """Whether BitCruise plans charging at all.

    Off does not mean "stop reporting": the deficit figures stay live, because
    knowing how much the car needs is useful whether or not something is
    scheduling it. What stops is deciding — nothing is proposed, and no plan is
    held approved.

    The state is persisted with the approval record rather than restored from
    the entity, so the coordinator knows it before the first evaluation.
    """

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the switch."""
        super().__init__(coordinator, "smart_charging")

    @property
    def is_on(self) -> bool:
        """Whether planning is enabled."""
        return self.coordinator.data.smart_charging

    async def async_turn_on(self, **kwargs: object) -> None:
        """Resume planning."""
        await self.coordinator.async_set_smart_charging(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Stop planning and release any approved plan."""
        await self.coordinator.async_set_smart_charging(False)
