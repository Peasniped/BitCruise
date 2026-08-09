"""End-to-end tests: real price attributes in, a charging window out."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from custom_components.bitcruise.const import (
    CONF_CAPACITY_ENTITY,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_READY_BY,
    CONF_RESERVE_FLOOR_PCT,
    CONF_SOC_ENTITY,
    CONF_TARGET_ENTITY,
    DOMAIN,
)
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

CPH = ZoneInfo("Europe/Copenhagen")
FIXTURE = Path(__file__).parent.parent / "fixtures" / "energidataservice.json"

SOC = "sensor.car_battery"
TARGET = "sensor.car_target"
CAPACITY = "sensor.car_capacity"
PRICE = "sensor.electricity_price"

SOURCES: dict[str, Any] = {
    CONF_SOC_ENTITY: SOC,
    CONF_TARGET_ENTITY: TARGET,
    CONF_CAPACITY_ENTITY: CAPACITY,
    CONF_PRICE_ENTITY: PRICE,
}
SETTINGS: dict[str, Any] = {
    CONF_CHARGING_POWER_KW: 11.0,
    CONF_CHARGING_EFFICIENCY: 90,
    CONF_RESERVE_FLOOR_PCT: 0,
    CONF_READY_BY: "07:00:00",
}


def _price_attributes() -> dict[str, Any]:
    """Attributes from the captured Energi Data Service sensor."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["attributes"]


async def _setup(
    hass: HomeAssistant,
    soc: str = "53",
    price_attributes: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set up an entry with the real price fixture.

    The instance timezone must match the price data. Ready-by is a wall-clock
    time, so a test instance left in another zone resolves "07:00" to a
    different instant and the planner correctly picks a different window.
    """
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    hass.states.async_set(SOC, soc, {"unit_of_measurement": "%"})
    hass.states.async_set(TARGET, "90", {"unit_of_measurement": "%"})
    hass.states.async_set(CAPACITY, "81.608", {"unit_of_measurement": "kWh"})
    hass.states.async_set(PRICE, "1.759", price_attributes or _price_attributes())

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=SOURCES,
        options=settings or SETTINGS,
        title="BitCruise",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH))
async def test_plan_picks_the_overnight_trough(hass: HomeAssistant) -> None:
    """From 18:00 with a 07:00 deadline, the window lands on 02:00-06:00.

    53% to 90% of 81.608 kWh needs 33.55 kWh from the grid, or 3.05 hours at
    11 kW. Whole intervals are allocated, so that is four hours, and the cheapest
    contiguous four on 10 August are 02:00-06:00 at 1.628, 1.586, 1.593, 1.739.
    """
    await _setup(hass)

    status = hass.states.get("sensor.bitcruise_plan_status")
    assert status.state == "proposed"
    assert status.attributes["problems"] == []
    assert status.attributes["price_intervals"] == 72

    start = hass.states.get("sensor.bitcruise_proposed_start").state
    end = hass.states.get("sensor.bitcruise_proposed_end").state
    assert datetime.fromisoformat(start).astimezone(CPH).hour == 2
    assert datetime.fromisoformat(end).astimezone(CPH).hour == 6


@freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH))
async def test_over_allocation_is_reported(hass: HomeAssistant) -> None:
    """Four whole hours can deliver 44 kWh; only 33.55 is needed."""
    await _setup(hass)

    status = hass.states.get("sensor.bitcruise_plan_status")
    assert status.attributes["over_allocation_kwh"] == pytest.approx(10.45, abs=0.01)


@freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH))
async def test_plan_reports_cost_in_the_price_currency(
    hass: HomeAssistant,
) -> None:
    """Cost must not be labelled in an assumed currency."""
    await _setup(hass)

    cost = hass.states.get("sensor.bitcruise_estimated_cost")
    assert cost.attributes["unit_of_measurement"] == "DKK"
    assert float(cost.state) == pytest.approx(53.83, abs=0.01)


@freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH))
async def test_plan_uses_actual_prices_when_available(
    hass: HomeAssistant,
) -> None:
    """Tomorrow is published, so the window rests on settled prices."""
    await _setup(hass)

    assert hass.states.get("sensor.bitcruise_price_quality").state == "actual"
    assert hass.states.get("binary_sensor.bitcruise_target_unreachable").state == "off"
    assert (
        float(hass.states.get("sensor.bitcruise_estimated_soc_at_ready").state) == 90.0
    )


@freeze_time(datetime(2026, 8, 10, 18, 0, tzinfo=CPH))
async def test_forecast_only_still_produces_a_plan(hass: HomeAssistant) -> None:
    """Before tomorrow's prices publish, a forecast-based plan is still useful."""
    attributes = _price_attributes()
    attributes["raw_today"] = []
    attributes["raw_tomorrow"] = []
    attributes["tomorrow_valid"] = False
    await _setup(hass, price_attributes=attributes)

    assert hass.states.get("sensor.bitcruise_price_quality").state == "forecast"
    assert hass.states.get("sensor.bitcruise_plan_status").state == "proposed"


@freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH))
async def test_short_horizon_reports_shortfall(hass: HomeAssistant) -> None:
    """A near-empty battery on a slow charger cannot be filled by 07:00.

    5% to 90% of 81.608 kWh is 77.1 kWh from the grid, which needs about 21
    hours at 3.7 kW. Only 13 hours remain before the deadline.
    """
    await _setup(hass, soc="5", settings={**SETTINGS, CONF_CHARGING_POWER_KW: 3.7})

    assert hass.states.get("binary_sensor.bitcruise_target_unreachable").state == "on"
    status = hass.states.get("sensor.bitcruise_plan_status")
    assert status.attributes["can_meet_target"] is False
    assert status.attributes["shortfall_kwh"] > 0


@freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH))
async def test_no_price_entity_leaves_deficits_but_no_window(
    hass: HomeAssistant,
) -> None:
    """Without prices the requirement is still known; the schedule is not."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    hass.states.async_set(SOC, "53", {"unit_of_measurement": "%"})
    hass.states.async_set(TARGET, "90", {"unit_of_measurement": "%"})
    hass.states.async_set(CAPACITY, "81.608", {"unit_of_measurement": "kWh"})

    sources = {k: v for k, v in SOURCES.items() if k != CONF_PRICE_ENTITY}
    entry = MockConfigEntry(
        domain=DOMAIN, data=sources, options=SETTINGS, title="BitCruise"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.bitcruise_plan_status").state == "needs_charge"
    assert hass.states.get("sensor.bitcruise_proposed_start").state == "unknown"
    assert float(hass.states.get("sensor.bitcruise_charging_deficit").state) == 37.0
