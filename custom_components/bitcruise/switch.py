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
    """Set up the BitCruise switches."""
    coordinator = entry.runtime_data
    async_add_entities(
        [BitCruiseSmartCharging(coordinator), BitCruiseChargerControl(coordinator)]
    )


class BitCruiseChargerControl(BitCruiseEntity, SwitchEntity):
    """Whether BitCruise may operate the charger, or only say what it would do.

    Off is the default, and is not a pause: every decision is still made and
    reported. It exists so the integration can be watched making the right calls
    for a few nights before it is allowed to make them — the one part of
    BitCruise that can physically do the wrong thing to a car.
    """

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the switch."""
        super().__init__(coordinator, "charger_control")

    @property
    def is_on(self) -> bool:
        """Whether charger actions are carried out."""
        return self.coordinator.data.execution_enabled

    @property
    def available(self) -> bool:
        """Unavailable until there is a charger control to operate."""
        return super().available and self.coordinator.data.capabilities.can_start

    async def async_turn_on(self, **kwargs: object) -> None:
        """Let BitCruise operate the charger."""
        await self.coordinator.async_set_execution_enabled(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Go back to deciding and reporting only."""
        await self.coordinator.async_set_execution_enabled(False)


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
