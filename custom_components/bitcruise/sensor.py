"""Sensors exposing the charge requirement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
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
from .models import PlanPriceQuality, PlanStatus

# Everything a BitCruise sensor can report.
type SensorValue = str | float | datetime | Decimal | None


@dataclass(frozen=True, kw_only=True)
class BitCruiseSensorDescription(SensorEntityDescription):
    """Describes a BitCruise sensor."""

    value_fn: Callable[[BitCruiseData], SensorValue]


def _requirement_value(data: BitCruiseData, attribute: str) -> float | None:
    """Read a field off the requirement, or None when there isn't one."""
    if data.requirement is None:
        return None
    return getattr(data.requirement, attribute)


def _plan_value(data: BitCruiseData, attribute: str) -> SensorValue:
    """Read a field off the effective plan, or None when there isn't one."""
    plan = data.effective_plan
    if plan is None:
        return None
    return getattr(plan, attribute)


def _proposal_value(data: BitCruiseData, attribute: str) -> SensorValue:
    """Read a field off the pending proposal only.

    Stays unknown when nothing is pending. "Proposed" is a question being asked,
    not a synonym for "planned" — falling back to the approved window here would
    make it impossible to tell whether an answer is wanted.
    """
    if data.record.proposal is None:
        return None
    return getattr(data.record.proposal, attribute)


def _approved_value(data: BitCruiseData, attribute: str) -> SensorValue:
    """Read a field off the approved plan, or None when none is approved."""
    if data.record.approved is None:
        return None
    return getattr(data.record.approved, attribute)


def _window_mean_price(data: BitCruiseData) -> Decimal | None:
    """Average price per kWh actually paid across the planned window."""
    plan = data.effective_plan
    if plan is None or plan.estimated_cost is None or plan.planned_grid_kwh <= 0:
        return None
    return plan.estimated_cost / Decimal(str(plan.planned_grid_kwh))


def _attribute_price(value: Decimal | None) -> float | None:
    """Render a price for a state attribute.

    Attributes are serialized with orjson, which refuses Decimal outright and
    takes the whole entity offline when it hits one. Currency is Decimal
    everywhere internally so repeated addition stays exact; that precision has
    done its job by the time a number is shown to a person.
    """
    if value is None:
        return None
    return round(float(value), 4)


def _rounded(value: SensorValue, digits: int | None) -> SensorValue:
    """Round a numeric state to the precision it is displayed at.

    ``suggested_display_precision`` only reaches the frontend; the state itself
    keeps whatever precision the calculation produced, which is how a cost lands
    on a dashboard as ``53.833372711111111884``. Rounding here makes the state
    and the display agree, and templates read the same number a person does.
    """
    if digits is None or not isinstance(value, (int, float, Decimal)):
        return value
    if digits == 0:
        return round(value)
    return round(value, digits)


SENSORS: tuple[BitCruiseSensorDescription, ...] = (
    BitCruiseSensorDescription(
        key="summary",
        translation_key="summary",
        value_fn=lambda data: data.summary,
    ),
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
        value_fn=lambda data: data.status,
    ),
    BitCruiseSensorDescription(
        key="proposed_start",
        translation_key="proposed_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _proposal_value(data, "start"),
    ),
    BitCruiseSensorDescription(
        key="proposed_end",
        translation_key="proposed_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _proposal_value(data, "end"),
    ),
    BitCruiseSensorDescription(
        key="approved_start",
        translation_key="approved_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _approved_value(data, "start"),
    ),
    BitCruiseSensorDescription(
        key="approved_end",
        translation_key="approved_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: _approved_value(data, "end"),
    ),
    BitCruiseSensorDescription(
        key="estimated_cost",
        translation_key="estimated_cost",
        suggested_display_precision=2,
        value_fn=lambda data: _plan_value(data, "estimated_cost"),
    ),
    BitCruiseSensorDescription(
        key="estimated_soc_at_ready",
        translation_key="estimated_soc_at_ready",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda data: _plan_value(data, "estimated_soc_at_end"),
    ),
    BitCruiseSensorDescription(
        key="ready_by",
        translation_key="ready_by",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.ready_by,
    ),
    BitCruiseSensorDescription(
        key="price_quality",
        translation_key="price_quality",
        device_class=SensorDeviceClass.ENUM,
        options=[quality.value for quality in PlanPriceQuality],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _plan_value(data, "price_quality"),
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
    def native_value(self) -> SensorValue:
        """Current value, or None when the requirement could not be computed."""
        value = self.entity_description.value_fn(self.coordinator.data)
        return _rounded(value, self.entity_description.suggested_display_precision)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Report cost in the currency the price entity uses.

        Assuming a currency would put a confident but wrong label on a number,
        so it stays unset until the price source states one.
        """
        if self.entity_description.key == "estimated_cost":
            return self.coordinator.data.currency
        return super().native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Explain the current state on the status sensor only.

        Attaching the diagnosis to every sensor would duplicate it six times; the
        status sensor is where a user looks when something is wrong.
        """
        if self.entity_description.key != "plan_status":
            return None
        data = self.coordinator.data
        plan = data.effective_plan
        prices = data.price_data
        record = data.record
        horizon_quality = prices.quality if prices else None
        return {
            "problems": list(data.problems),
            "plug_status": data.plug_status.value,
            "data_freshness": data.freshness.value,
            "current_soc_pct": data.current_soc_pct,
            "target_soc_pct": data.target_soc_pct,
            "usable_capacity_kwh": data.usable_capacity_kwh,
            "ready_by": data.ready_by.isoformat() if data.ready_by else None,
            # What the price adapter made of the selected entity. A misread
            # curve produces a confident, plausible, wrong schedule, so the
            # parse is reported rather than assumed (DESIGN.md 12).
            "price_intervals": data.price_interval_count,
            "price_source": prices.source if prices else None,
            "price_horizon_quality": horizon_quality.value if horizon_quality else None,
            "price_tomorrow_valid": prices.tomorrow_valid if prices else None,
            # Which plan is being described, and whether an answer is wanted.
            "plan_id": plan.id if plan else None,
            "approved_plan_id": record.approved.id if record.approved else None,
            "proposed_plan_id": record.proposal.id if record.proposal else None,
            "proposal_reason": (
                record.proposal_reason.value if record.proposal_reason else None
            ),
            "replaces_approved_plan": record.is_replacement,
            "smart_charging": data.smart_charging,
            "can_meet_target": plan.can_meet_target if plan else None,
            "shortfall_kwh": plan.shortfall_kwh if plan else None,
            "over_allocation_kwh": plan.over_allocation_kwh if plan else None,
            # These two together explain why a window was chosen. When the mean
            # is well above the cheapest price available, the ready-by deadline
            # ruled the cheap period out rather than the planner missing it.
            "window_mean_price": _attribute_price(_window_mean_price(data)),
            "cheapest_price_in_horizon": _attribute_price(
                data.cheapest_price_in_horizon
            ),
        }
