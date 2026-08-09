"""Every BitCruise state must survive the trip to the frontend.

Home Assistant serializes states with orjson for the websocket and the recorder.
orjson refuses a Decimal outright, and the entity that produced it then never
reaches the UI — it simply shows as unavailable, with nothing on the entity
itself to suggest why. Currency is Decimal throughout this integration, so this
is a standing hazard rather than a one-off mistake, and it only appears once
there is a plan to describe: with no prices the offending attributes are None
and everything looks fine.

Reading attributes in-process, as the other tests do, does not catch it.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from custom_components.bitcruise.const import (
    CONF_APPROVAL_POLICY,
    CONF_AVAILABILITY_ENTITY,
    CONF_CAPACITY_ENTITY,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
    CONF_PLUG_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_READY_BY,
    CONF_RESERVE_FLOOR_PCT,
    CONF_SOC_ENTITY,
    CONF_TARGET_ENTITY,
    DOMAIN,
)
from custom_components.bitcruise.models import ApprovalPolicy
from freezegun import freeze_time
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.json import json_bytes
from pytest_homeassistant_custom_component.common import MockConfigEntry

CPH = ZoneInfo("Europe/Copenhagen")
FIXTURE = Path(__file__).parent.parent / "fixtures" / "energidataservice.json"
EVENING = datetime(2026, 8, 9, 18, 0, tzinfo=CPH)

SOC = "sensor.car_battery"
TARGET = "sensor.car_target"
CAPACITY = "sensor.car_capacity"
PLUG = "sensor.car_plug"
AVAILABILITY = "sensor.car_connection"
PRICE = "sensor.electricity_price"


async def _setup(hass: HomeAssistant, policy: ApprovalPolicy) -> None:
    """Set up an entry with every optional source bound and real prices."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    hass.states.async_set(SOC, "53", {"unit_of_measurement": "%"})
    hass.states.async_set(TARGET, "90", {"unit_of_measurement": "%"})
    hass.states.async_set(CAPACITY, "81.608", {"unit_of_measurement": "kWh"})
    hass.states.async_set(PLUG, "connected")
    hass.states.async_set(AVAILABILITY, "available")
    hass.states.async_set(
        PRICE, "1.759", json.loads(FIXTURE.read_text(encoding="utf-8"))["attributes"]
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOC_ENTITY: SOC,
            CONF_TARGET_ENTITY: TARGET,
            CONF_CAPACITY_ENTITY: CAPACITY,
            CONF_PLUG_ENTITY: PLUG,
            CONF_AVAILABILITY_ENTITY: AVAILABILITY,
            CONF_PRICE_ENTITY: PRICE,
        },
        options={
            CONF_CHARGING_POWER_KW: 11.0,
            CONF_CHARGING_EFFICIENCY: 90,
            CONF_RESERVE_FLOOR_PCT: 0,
            CONF_READY_BY: "07:00:00",
            CONF_APPROVAL_POLICY: policy.value,
        },
        title="BitCruise",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _bitcruise_states(hass: HomeAssistant) -> list[State]:
    """Every entity this integration publishes."""
    states = [
        state
        for state in hass.states.async_all()
        if state.entity_id.split(".")[1].startswith("bitcruise_")
    ]
    assert states, "no BitCruise entities were created"
    return states


def _assert_serializable(states: list[State]) -> None:
    """Fail naming the offending entity and attribute, not just the type."""
    for state in states:
        for key, value in state.attributes.items():
            try:
                json_bytes({key: value})
            except TypeError as err:
                pytest.fail(
                    f"{state.entity_id} attribute {key!r} is not serializable: "
                    f"{value!r} ({type(value).__name__}) — {err}"
                )
        json_bytes(state.as_dict())


@pytest.mark.parametrize("policy", list(ApprovalPolicy))
async def test_every_state_survives_json(
    hass: HomeAssistant, policy: ApprovalPolicy
) -> None:
    """With a real plan in hand, under both approval policies."""
    with freeze_time(EVENING):
        await _setup(hass, policy)
        _assert_serializable(_bitcruise_states(hass))


async def test_still_serializable_once_a_plan_is_approved(
    hass: HomeAssistant,
) -> None:
    """Accepting swaps which plan the attributes describe."""
    with freeze_time(EVENING):
        await _setup(hass, ApprovalPolicy.ALWAYS_ASK)
        await hass.services.async_call(
            "button",
            "press",
            {ATTR_ENTITY_ID: "button.bitcruise_accept_plan"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert hass.states.get("sensor.bitcruise_plan_status").state == "approved"
        _assert_serializable(_bitcruise_states(hass))


async def test_still_serializable_with_no_price_data(hass: HomeAssistant) -> None:
    """The state the integration is in before its sources have loaded."""
    with freeze_time(EVENING):
        await _setup(hass, ApprovalPolicy.ASK_ON_CHANGE)
        hass.states.async_set(PRICE, "unavailable", {})
        await hass.async_block_till_done()

        _assert_serializable(_bitcruise_states(hass))


async def test_the_price_attributes_are_numbers(hass: HomeAssistant) -> None:
    """Named explicitly, because this is the pair that took the sensor offline."""
    with freeze_time(EVENING):
        await _setup(hass, ApprovalPolicy.ASK_ON_CHANGE)

        attributes: dict[str, Any] = dict(
            hass.states.get("sensor.bitcruise_plan_status").attributes
        )
        assert isinstance(attributes["window_mean_price"], float)
        assert isinstance(attributes["cheapest_price_in_horizon"], float)
        assert attributes["cheapest_price_in_horizon"] == pytest.approx(0.457)
