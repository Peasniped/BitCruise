"""Sensors exposing the charge requirement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BitCruiseConfigEntry, BitCruiseCoordinator, BitCruiseData
from .entity import BitCruiseEntity
from .models import PlanStatus


@dataclass(frozen=True, kw_only=True)
class BitCruiseSensorDescription(SensorEntityDescription):
    """Describes a BitCruise sensor."""

    value_fn: Callable[[BitCruiseData], float | str | None]


def _requirement_value(data: BitCruiseData, attribute: str) -> float | None:
    """Read a field off the requirement, or None when there isn't one."""
    if data.requirement is None:
        return None
    return getattr(data.requirement, attribute)


def _plan_status(data: BitCruiseData) -> str:
    """Summarise the current state.

    ERROR means a required input is missing, which is deliberately distinct from
    "no charge needed": the first is a configuration or availability problem, the
    second is a healthy outcome.
    """
    if data.requirement is None:
        return PlanStatus.ERROR
    if not data.requirement.is_charge_needed:
        return PlanStatus.IDLE
    return PlanStatus.NEEDS_CHARGE


SENSORS: tuple[BitCruiseSensorDescription, ...] = (
    BitCruiseSensorDescription(
        key="charging_deficit",
        translation_key="charging_deficit",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda data: _requirement_value(data, "deficit_pct"),
    ),
    BitCruiseSensorDescription(
        key="battery_energy_deficit",
        translation_key="battery_energy_deficit",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda data: _requirement_value(data, "battery_deficit_kwh"),
    ),
    BitCruiseSensorDescription(
        key="grid_energy_required",
        translation_key="grid_energy_required",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda data: _requirement_value(data, "grid_energy_required_kwh"),
    ),
    BitCruiseSensorDescription(
        key="required_charge_duration",
        translation_key="required_charge_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=2,
        value_fn=lambda data: _requirement_value(data, "required_hours"),
    ),
    BitCruiseSensorDescription(
        key="reserve_floor_deficit",
        translation_key="reserve_floor_deficit",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _requirement_value(data, "floor_deficit_kwh"),
    ),
    BitCruiseSensorDescription(
        key="plan_status",
        translation_key="plan_status",
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in PlanStatus],
        value_fn=_plan_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BitCruiseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BitCruise sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        BitCruiseSensor(coordinator, description) for description in SENSORS
    )


class BitCruiseSensor(BitCruiseEntity, SensorEntity):
    """A single derived value."""

    entity_description: BitCruiseSensorDescription

    def __init__(
        self,
        coordinator: BitCruiseCoordinator,
        description: BitCruiseSensorDescription,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        """Current value, or None when the requirement could not be computed."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Explain the current state on the status sensor only.

        Attaching the diagnosis to every sensor would duplicate it six times; the
        status sensor is where a user looks when something is wrong.
        """
        if self.entity_description.key != "plan_status":
            return None
        data = self.coordinator.data
        return {
            "problems": list(data.problems),
            "plug_status": data.plug_status.value,
            "data_freshness": data.freshness.value,
            "current_soc_pct": data.current_soc_pct,
            "target_soc_pct": data.target_soc_pct,
            "usable_capacity_kwh": data.usable_capacity_kwh,
            "ready_by": data.ready_by.isoformat() if data.ready_by else None,
        }
