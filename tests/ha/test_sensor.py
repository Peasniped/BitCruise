"""Tests for the BitCruise entities.

Values mirror the reference installation: an 81.608 kWh battery at 47%, targeting
90%, charging at 11 kW with 90% efficiency.
"""

from typing import Any

from custom_components.bitcruise.const import (
    CONF_AVAILABILITY_ENTITY,
    CONF_CAPACITY_ENTITY,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
    CONF_PLUG_ENTITY,
    CONF_READY_BY,
    CONF_RESERVE_FLOOR_PCT,
    CONF_SOC_ENTITY,
    CONF_TARGET_ENTITY,
    DOMAIN,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

SOC = "sensor.car_battery"
TARGET = "sensor.car_target"
CAPACITY = "sensor.car_capacity"
PLUG = "sensor.car_plug"
AVAILABILITY = "sensor.car_availability"

SOURCES: dict[str, Any] = {
    CONF_SOC_ENTITY: SOC,
    CONF_TARGET_ENTITY: TARGET,
    CONF_CAPACITY_ENTITY: CAPACITY,
    CONF_PLUG_ENTITY: PLUG,
    CONF_AVAILABILITY_ENTITY: AVAILABILITY,
}
SETTINGS: dict[str, Any] = {
    CONF_CHARGING_POWER_KW: 11.0,
    CONF_CHARGING_EFFICIENCY: 90,
    CONF_RESERVE_FLOOR_PCT: 0,
    CONF_READY_BY: "07:00:00",
}


def _set_sources(
    hass: HomeAssistant,
    soc: str = "47",
    target: str = "90",
    plug: str = "disconnected",
    availability: str = "available",
) -> None:
    """Populate the source entities."""
    hass.states.async_set(SOC, soc, {"unit_of_measurement": "%"})
    hass.states.async_set(TARGET, target, {"unit_of_measurement": "%"})
    hass.states.async_set(CAPACITY, "81.608", {"unit_of_measurement": "kWh"})
    hass.states.async_set(PLUG, plug)
    hass.states.async_set(AVAILABILITY, availability)


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a configured entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=SOURCES, options=SETTINGS, title="BitCruise"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_deficit_sensors(hass: HomeAssistant) -> None:
    """47% to 90% of 81.608 kWh is 43 points, 35.09 kWh, 39.0 kWh from the grid.

    States are rounded to the precision they are displayed at. Home Assistant
    only applies ``suggested_display_precision`` in the frontend, so without
    that the state a template reads is ``35.091440000000004``.
    """
    _set_sources(hass)
    await _setup(hass)

    assert hass.states.get("sensor.bitcruise_charging_deficit").state == "43"
    assert hass.states.get("sensor.bitcruise_battery_energy_deficit").state == "35.1"
    assert hass.states.get("sensor.bitcruise_grid_energy_required").state == "39.0"
    assert hass.states.get("sensor.bitcruise_required_charge_duration").state == "3.54"


async def test_charge_needed_and_status(hass: HomeAssistant) -> None:
    """Below target means charge needed."""
    _set_sources(hass)
    await _setup(hass)

    assert hass.states.get("binary_sensor.bitcruise_charge_needed").state == "on"
    assert hass.states.get("sensor.bitcruise_plan_status").state == "needs_charge"


async def test_at_target_is_idle(hass: HomeAssistant) -> None:
    """At or above target there is no deficit and no charge needed."""
    _set_sources(hass, soc="90")
    await _setup(hass)

    assert float(hass.states.get("sensor.bitcruise_charging_deficit").state) == 0.0
    assert hass.states.get("binary_sensor.bitcruise_charge_needed").state == "off"
    assert hass.states.get("sensor.bitcruise_plan_status").state == "idle"


async def test_recomputes_when_soc_changes(hass: HomeAssistant) -> None:
    """The coordinator is event driven, not polled."""
    _set_sources(hass)
    await _setup(hass)
    assert float(hass.states.get("sensor.bitcruise_charging_deficit").state) == 43.0

    hass.states.async_set(SOC, "60", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.bitcruise_charging_deficit").state) == 30.0


async def test_unavailable_source_reports_error(hass: HomeAssistant) -> None:
    """A missing input must be visible, not silently treated as zero."""
    _set_sources(hass, soc="unavailable")
    await _setup(hass)

    status = hass.states.get("sensor.bitcruise_plan_status")
    assert status.state == "error"
    assert any("car_battery" in problem for problem in status.attributes["problems"])
    assert hass.states.get("sensor.bitcruise_charging_deficit").state == "unknown"


async def test_stale_vehicle_data_is_flagged(hass: HomeAssistant) -> None:
    """Power saving mode means the reported SoC may be out of date."""
    _set_sources(hass, availability="power_saving_mode")
    await _setup(hass)

    status = hass.states.get("sensor.bitcruise_plan_status")
    assert status.attributes["data_freshness"] == "stale"
    assert any("stale" in problem for problem in status.attributes["problems"])


async def test_plug_fault_is_not_reported_as_disconnected(
    hass: HomeAssistant,
) -> None:
    """A charging fault must not be hidden as 'not plugged in'."""
    _set_sources(hass, plug="fault")
    await _setup(hass)

    assert (
        hass.states.get("binary_sensor.bitcruise_vehicle_connected").state == "unknown"
    )
    assert (
        hass.states.get("sensor.bitcruise_plan_status").attributes["plug_status"]
        == "fault"
    )


async def test_bad_reserve_floor_does_not_blank_the_deficit(
    hass: HomeAssistant,
) -> None:
    """An invalid floor is reported, but must not withhold correct figures.

    The floor does not affect how much energy the target needs, so letting it
    blank every sensor would hide good information over an unrelated setting.
    """
    _set_sources(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=SOURCES,
        options={**SETTINGS, CONF_RESERVE_FLOOR_PCT: 95},
        title="BitCruise",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get("sensor.bitcruise_charging_deficit").state) == 43.0
    status = hass.states.get("sensor.bitcruise_plan_status")
    assert status.state == "needs_charge"
    assert any("reserve floor" in problem for problem in status.attributes["problems"])


async def test_summary_without_prices_says_what_is_missing(
    hass: HomeAssistant,
) -> None:
    """No price entity is configured here, so no window can exist yet."""
    _set_sources(hass)
    await _setup(hass)

    assert hass.states.get("sensor.bitcruise_summary").state == (
        "Needs 39.0 kWh from the grid; waiting for electricity prices."
    )


async def test_summary_at_target_is_not_an_error(hass: HomeAssistant) -> None:
    _set_sources(hass, soc="90")
    await _setup(hass)

    assert hass.states.get("sensor.bitcruise_summary").state == (
        "No charging needed; battery is at 90% of a 90% target."
    )


async def test_summary_names_the_first_problem(hass: HomeAssistant) -> None:
    """An error state alone sends the user off to attributes to find out what."""
    _set_sources(hass, soc="unavailable")
    await _setup(hass)

    summary = hass.states.get("sensor.bitcruise_summary").state
    assert summary.startswith("Cannot plan: ")
    assert "car_battery" in summary


async def test_plug_connected(hass: HomeAssistant) -> None:
    """A connected cable reads as on."""
    _set_sources(hass, plug="connected")
    await _setup(hass)

    assert hass.states.get("binary_sensor.bitcruise_vehicle_connected").state == "on"
