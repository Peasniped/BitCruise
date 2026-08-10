"""Binary sensors for BitCruise."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BitCruiseConfigEntry, BitCruiseCoordinator
from .entity import BitCruiseEntity
from .source_normalization import PlugStatus


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BitCruiseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BitCruise binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            BitCruiseChargeNeeded(coordinator),
            BitCruiseVehicleConnected(coordinator),
            BitCruiseCanMeetTarget(coordinator),
            BitCruisePlanRequiresApproval(coordinator),
            BitCruiseReadyToCharge(coordinator),
        ]
    )


class BitCruiseReadyToCharge(BitCruiseEntity, BinarySensorEntity):
    """Whether charging could begin if a window opened right now.

    Independent of the plan and the clock on purpose: a car left unplugged at
    bedtime shows here hours before it misses the window it was booked for.
    """

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the entity."""
        super().__init__(coordinator, "ready_to_charge")

    @property
    def is_on(self) -> bool | None:
        """True when the hardware is ready, None when nothing can say."""
        return self.coordinator.data.ready_to_charge

    @property
    def available(self) -> bool:
        """Unavailable until a charger start control is configured.

        Reporting "not ready" with no charger configured would imply a fault
        where there is simply no feature.
        """
        return super().available and self.coordinator.data.capabilities.can_start


class BitCruisePlanRequiresApproval(BitCruiseEntity, BinarySensorEntity):
    """Whether a proposed plan is waiting for an answer.

    This carries the state that a notification only announces. A notification
    can be missed or dismissed; something has to remain true for as long as the
    question is open, so a dashboard or an automation can act on it.
    """

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the entity."""
        super().__init__(coordinator, "plan_requires_approval")

    @property
    def is_on(self) -> bool:
        """True while a proposal is pending."""
        return self.coordinator.data.record.requires_approval


class BitCruiseCanMeetTarget(BitCruiseEntity, BinarySensorEntity):
    """Whether the plan reaches the charge target before the deadline."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the entity."""
        super().__init__(coordinator, "target_unreachable")

    @property
    def is_on(self) -> bool | None:
        """True when the target cannot be met.

        Reported as a problem rather than as "can meet target" so it is off in
        the healthy case and draws attention only when the car will fall short.
        """
        plan = self.coordinator.data.effective_plan
        if plan is None:
            return None
        return not plan.can_meet_target


class BitCruiseChargeNeeded(BitCruiseEntity, BinarySensorEntity):
    """Whether the vehicle is below its charge target."""

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the entity."""
        super().__init__(coordinator, "charge_needed")

    @property
    def is_on(self) -> bool | None:
        """True when charging is required, None when it cannot be determined."""
        requirement = self.coordinator.data.requirement
        if requirement is None:
            return None
        return requirement.is_charge_needed


class BitCruiseVehicleConnected(BitCruiseEntity, BinarySensorEntity):
    """Whether the charging cable is connected."""

    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the entity."""
        super().__init__(coordinator, "vehicle_connected")

    @property
    def is_on(self) -> bool | None:
        """True when connected.

        A fault reports None rather than False. "Not charging because of a fault"
        and "not plugged in" are different situations, and collapsing them would
        hide the one worth acting on. The fault itself is surfaced in the plan
        status attributes.
        """
        status = self.coordinator.data.plug_status
        if status is PlugStatus.CONNECTED:
            return True
        if status is PlugStatus.DISCONNECTED:
            return False
        return None

    @property
    def available(self) -> bool:
        """Unavailable when no plug entity is configured."""
        return (
            super().available
            and self.coordinator.data.plug_status is not PlugStatus.UNKNOWN
        )
