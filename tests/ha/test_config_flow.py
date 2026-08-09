"""Tests for the BitCruise config flow and entry setup/unload."""

from typing import Any

import pytest
from custom_components.bitcruise.const import (
    CONF_CAPACITY_FIXED_KWH,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
    CONF_READY_BY,
    CONF_RESERVE_FLOOR_PCT,
    CONF_SOC_ENTITY,
    CONF_TARGET_ENTITY,
    CONF_TARGET_FIXED_PCT,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

SOC_ENTITY = "sensor.test_battery"
TARGET_ENTITY = "sensor.test_target"

SOURCES: dict[str, Any] = {CONF_SOC_ENTITY: SOC_ENTITY}
SETTINGS: dict[str, Any] = {
    CONF_TARGET_FIXED_PCT: 80,
    CONF_CAPACITY_FIXED_KWH: 81.608,
    CONF_CHARGING_POWER_KW: 11.0,
    CONF_CHARGING_EFFICIENCY: 90,
    CONF_RESERVE_FLOOR_PCT: 0,
    CONF_READY_BY: "07:00:00",
}


@pytest.fixture(autouse=True)
def _sources(hass: HomeAssistant) -> None:
    """Provide the source entities the flow and coordinator expect."""
    hass.states.async_set(
        SOC_ENTITY, "47", {"unit_of_measurement": "%", "device_class": "battery"}
    )
    hass.states.async_set(TARGET_ENTITY, "90", {"unit_of_measurement": "%"})


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """The two-step flow collects sources then settings and creates one entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SOURCES)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], SETTINGS)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BitCruise"
    assert result["data"][CONF_SOC_ENTITY] == SOC_ENTITY
    assert result["options"][CONF_CAPACITY_FIXED_KWH] == 81.608


async def test_capacity_is_required_without_an_entity(hass: HomeAssistant) -> None:
    """Refusing to guess a battery capacity is deliberate."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SOURCES)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SETTINGS, CONF_CAPACITY_FIXED_KWH: 0}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CAPACITY_FIXED_KWH: "capacity_required"}


async def test_reserve_floor_above_target_is_rejected(hass: HomeAssistant) -> None:
    """The floor is a lower bound on being drivable, not a second target."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], SOURCES)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**SETTINGS, CONF_TARGET_FIXED_PCT: 80, CONF_RESERVE_FLOOR_PCT: 90},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_RESERVE_FLOOR_PCT: "floor_above_target"}


async def test_target_entity_removes_the_fixed_requirement(
    hass: HomeAssistant,
) -> None:
    """A selected target entity means no fixed target is needed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SOURCES, CONF_TARGET_ENTITY: TARGET_ENTITY}
    )
    settings = {k: v for k, v in SETTINGS.items() if k != CONF_TARGET_FIXED_PCT}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], settings)
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TARGET_ENTITY] == TARGET_ENTITY


async def test_target_measured_in_amps_is_rejected(hass: HomeAssistant) -> None:
    """A charging current limit is not a charge target.

    Vehicle integrations expose both as plain numbers. Picking the current limit
    yields a target of 32 that looks entirely reasonable until nothing charges.
    """
    hass.states.async_set(
        "sensor.car_charging_limit", "32", {"unit_of_measurement": "A"}
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {**SOURCES, CONF_TARGET_ENTITY: "sensor.car_charging_limit"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_TARGET_ENTITY: "target_not_a_percentage"}


async def test_floor_checked_against_target_entity(hass: HomeAssistant) -> None:
    """The floor clash must be caught even when the target comes from an entity."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SOURCES, CONF_TARGET_ENTITY: TARGET_ENTITY}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**SETTINGS, CONF_RESERVE_FLOOR_PCT: 95}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_RESERVE_FLOOR_PCT: "floor_above_target"}


async def test_single_instance_only(hass: HomeAssistant) -> None:
    """A second config entry is rejected because the integration is single-instance."""
    MockConfigEntry(domain=DOMAIN, data=SOURCES, options=SETTINGS).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """A config entry loads and unloads cleanly."""
    entry = MockConfigEntry(
        domain=DOMAIN, data=SOURCES, options=SETTINGS, title="BitCruise"
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_options_flow_updates_settings(hass: HomeAssistant) -> None:
    """Settings can be changed without reselecting entities."""
    entry = MockConfigEntry(domain=DOMAIN, data=SOURCES, options=SETTINGS)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SETTINGS, CONF_TARGET_FIXED_PCT: 70}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_TARGET_FIXED_PCT] == 70
