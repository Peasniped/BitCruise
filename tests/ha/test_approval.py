"""The approval machinery as a user meets it: buttons, a switch, and restarts.

The pure rules are covered in ``tests/test_plan_state.py``. What matters here is
that the entities are wired to those rules, and that an approval survives a
Home Assistant restart.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.bitcruise.const import (
    CONF_APPROVAL_POLICY,
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
from custom_components.bitcruise.models import ApprovalPolicy
from freezegun import freeze_time
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

CPH = ZoneInfo("Europe/Copenhagen")
FIXTURE = Path(__file__).parent.parent / "fixtures" / "energidataservice.json"
EVENING = datetime(2026, 8, 9, 18, 0, tzinfo=CPH)

SOC = "sensor.car_battery"
TARGET = "sensor.car_target"
CAPACITY = "sensor.car_capacity"
PRICE = "sensor.electricity_price"

STATUS = "sensor.bitcruise_plan_status"
SUMMARY = "sensor.bitcruise_summary"
POLICY = "select.bitcruise_when_to_ask_before_charging"
ACCEPT = "button.bitcruise_accept_plan"
REJECT = "button.bitcruise_reject_plan"
RECALCULATE = "button.bitcruise_recalculate_plan"
PROPOSED_START = "sensor.bitcruise_proposed_start"
APPROVED_START = "sensor.bitcruise_approved_start"
NEEDS_APPROVAL = "binary_sensor.bitcruise_plan_requires_approval"
SMART_CHARGING = "switch.bitcruise_smart_charging"

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
    CONF_APPROVAL_POLICY: ApprovalPolicy.ALWAYS_ASK.value,
}


def _price_attributes() -> dict[str, Any]:
    """Attributes from the captured Energi Data Service sensor."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["attributes"]


async def _setup(
    hass: HomeAssistant,
    settings: dict[str, Any] | None = None,
    entry: MockConfigEntry | None = None,
) -> MockConfigEntry:
    """Set up an entry against the real price fixture."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    hass.states.async_set(SOC, "53", {"unit_of_measurement": "%"})
    hass.states.async_set(TARGET, "90", {"unit_of_measurement": "%"})
    hass.states.async_set(CAPACITY, "81.608", {"unit_of_measurement": "kWh"})
    hass.states.async_set(PRICE, "1.759", _price_attributes())

    if entry is None:
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


async def _press(hass: HomeAssistant, button: str) -> None:
    """Press one of the approval buttons."""
    await hass.services.async_call(
        "button",
        "press",
        {ATTR_ENTITY_ID: f"button.bitcruise_{button}"},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _set_policy(hass: HomeAssistant, policy: ApprovalPolicy) -> None:
    """Change the approval policy through the select entity."""
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: POLICY, "option": policy.value},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _switch(hass: HomeAssistant, service: str) -> None:
    """Flip the smart charging switch."""
    await hass.services.async_call(
        "switch", service, {ATTR_ENTITY_ID: SMART_CHARGING}, blocking=True
    )
    await hass.async_block_till_done()


async def test_always_ask_proposes_and_waits(hass: HomeAssistant) -> None:
    """Nothing is approved until the button is pressed."""
    with freeze_time(EVENING):
        await _setup(hass)

        assert hass.states.get(STATUS).state == "awaiting_approval"
        assert hass.states.get(NEEDS_APPROVAL).state == "on"
        assert hass.states.get(APPROVED_START).state == "unknown"
        assert (
            datetime.fromisoformat(hass.states.get(PROPOSED_START).state)
            .astimezone(CPH)
            .hour
            == 2
        )


async def test_accepting_approves_the_window(hass: HomeAssistant) -> None:
    with freeze_time(EVENING):
        await _setup(hass)
        proposed = hass.states.get(PROPOSED_START).state

        await _press(hass, "accept_plan")

        assert hass.states.get(STATUS).state == "approved"
        assert hass.states.get(NEEDS_APPROVAL).state == "off"
        assert hass.states.get(APPROVED_START).state == proposed
        assert hass.states.get(PROPOSED_START).state == "unknown"


async def test_rejecting_leaves_nothing_scheduled(hass: HomeAssistant) -> None:
    """And the same window is not offered again on the next evaluation."""
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "reject_plan")

        assert hass.states.get(NEEDS_APPROVAL).state == "off"
        assert hass.states.get(STATUS).state == "needs_charge"

        hass.states.async_set(SOC, "53", {"unit_of_measurement": "%", "poke": 1})
        await hass.async_block_till_done()

        assert hass.states.get(NEEDS_APPROVAL).state == "off"


async def test_recalculate_reconsiders_a_rejected_window(
    hass: HomeAssistant,
) -> None:
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "reject_plan")
        assert hass.states.get(NEEDS_APPROVAL).state == "off"

        await _press(hass, "recalculate_plan")

        assert hass.states.get(NEEDS_APPROVAL).state == "on"
        assert hass.states.get(STATUS).attributes["proposal_reason"] == "manual"


async def test_an_approved_plan_is_not_moved_by_a_replan(
    hass: HomeAssistant,
) -> None:
    """ADR-003, end to end: cheaper prices stage a proposal, they do not act.

    The car's state of charge drops, which makes a different window optimal.
    The approved window must stay exactly where it was until someone answers.
    """
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")
        approved = hass.states.get(APPROVED_START).state

        hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()

        assert hass.states.get(APPROVED_START).state == approved
        assert hass.states.get(NEEDS_APPROVAL).state == "on"
        assert hass.states.get(STATUS).attributes["replaces_approved_plan"] is True
        assert hass.states.get(STATUS).attributes["proposal_reason"] == "soc_change"

        await _press(hass, "accept_plan")
        assert hass.states.get(APPROVED_START).state != approved
        assert hass.states.get(NEEDS_APPROVAL).state == "off"


async def test_keeping_the_old_plan_discards_the_replacement(
    hass: HomeAssistant,
) -> None:
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")
        approved = hass.states.get(APPROVED_START).state

        hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()
        await _press(hass, "reject_plan")

        assert hass.states.get(APPROVED_START).state == approved
        assert hass.states.get(STATUS).state == "approved"


async def test_ask_on_change_approves_the_first_plan(hass: HomeAssistant) -> None:
    with freeze_time(EVENING):
        await _setup(
            hass,
            settings={
                **SETTINGS,
                CONF_APPROVAL_POLICY: ApprovalPolicy.ASK_ON_CHANGE.value,
            },
        )

        assert hass.states.get(STATUS).state == "approved"
        assert hass.states.get(NEEDS_APPROVAL).state == "off"


async def test_smart_charging_off_releases_everything(hass: HomeAssistant) -> None:
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")
        assert hass.states.get(STATUS).state == "approved"

        await _switch(hass, "turn_off")

        assert hass.states.get(SMART_CHARGING).state == "off"
        assert hass.states.get(STATUS).state == "idle"
        assert hass.states.get(APPROVED_START).state == "unknown"
        # The deficit figures are still useful with planning switched off.
        assert float(hass.states.get("sensor.bitcruise_charging_deficit").state) == 37.0


async def test_smart_charging_back_on_plans_again(hass: HomeAssistant) -> None:
    with freeze_time(EVENING):
        await _setup(hass)
        await _switch(hass, "turn_off")
        await _switch(hass, "turn_on")

        assert hass.states.get(NEEDS_APPROVAL).state == "on"


async def test_an_approval_survives_a_restart(hass: HomeAssistant) -> None:
    """Scheduled callbacks do not survive a restart; the approval must."""
    with freeze_time(EVENING):
        entry = await _setup(hass)
        await _press(hass, "accept_plan")
        approved = hass.states.get(APPROVED_START).state

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        await _setup(hass, entry=entry)

        assert hass.states.get(STATUS).state == "approved"
        assert hass.states.get(APPROVED_START).state == approved
        assert hass.states.get(NEEDS_APPROVAL).state == "off"


async def test_smart_charging_off_survives_a_restart(hass: HomeAssistant) -> None:
    """Otherwise a restart would quietly resume planning the user had stopped."""
    with freeze_time(EVENING):
        entry = await _setup(hass)
        await _switch(hass, "turn_off")

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        await _setup(hass, entry=entry)

        assert hass.states.get(SMART_CHARGING).state == "off"
        assert hass.states.get(STATUS).state == "idle"


async def test_a_rejection_survives_a_restart(hass: HomeAssistant) -> None:
    """A restart must not re-ask a question the user already answered."""
    with freeze_time(EVENING):
        entry = await _setup(hass)
        await _press(hass, "reject_plan")

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        await _setup(hass, entry=entry)

        assert hass.states.get(NEEDS_APPROVAL).state == "off"


async def test_the_policy_select_starts_from_the_configured_value(
    hass: HomeAssistant,
) -> None:
    """The setting moved out of the options flow; existing entries keep theirs."""
    with freeze_time(EVENING):
        await _setup(hass)

        assert hass.states.get(POLICY).state == ApprovalPolicy.ALWAYS_ASK.value
        assert hass.states.get(POLICY).attributes["options"] == [
            "always_ask",
            "ask_on_change",
            "automatic",
        ]


async def test_automatic_answers_a_pending_question(hass: HomeAssistant) -> None:
    """Switching to never-ask must not leave a prompt nothing will answer."""
    with freeze_time(EVENING):
        await _setup(hass)
        assert hass.states.get(NEEDS_APPROVAL).state == "on"
        proposed = hass.states.get(PROPOSED_START).state

        await _set_policy(hass, ApprovalPolicy.AUTOMATIC)

        assert hass.states.get(NEEDS_APPROVAL).state == "off"
        assert hass.states.get(STATUS).state == "approved"
        assert hass.states.get(APPROVED_START).state == proposed


async def test_automatic_takes_a_moved_window_without_asking(
    hass: HomeAssistant,
) -> None:
    """The deliberate ADR-003 relaxation, and the point of the setting."""
    with freeze_time(EVENING):
        await _setup(hass)
        await _set_policy(hass, ApprovalPolicy.AUTOMATIC)
        approved = hass.states.get(APPROVED_START).state

        hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()

        assert hass.states.get(APPROVED_START).state != approved
        assert hass.states.get(NEEDS_APPROVAL).state == "off"
        assert hass.states.get(STATUS).state == "approved"


async def test_the_policy_survives_a_restart(hass: HomeAssistant) -> None:
    """It is no longer in the config entry, so it has to be in the store."""
    with freeze_time(EVENING):
        entry = await _setup(hass)
        await _set_policy(hass, ApprovalPolicy.AUTOMATIC)

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        await _setup(hass, entry=entry)

        assert hass.states.get(POLICY).state == ApprovalPolicy.AUTOMATIC.value
        assert hass.states.get(NEEDS_APPROVAL).state == "off"


async def test_recalculate_says_when_it_changed_nothing(hass: HomeAssistant) -> None:
    """Otherwise the button looks broken: the same plan changes no entity."""
    with freeze_time(EVENING):
        await _setup(
            hass,
            settings={
                **SETTINGS,
                CONF_APPROVAL_POLICY: ApprovalPolicy.ASK_ON_CHANGE.value,
            },
        )
        before = hass.states.get(SUMMARY).state
        assert before.startswith("Charging ")

        await _press(hass, "recalculate_plan")

        after = hass.states.get(SUMMARY).state
        assert after == f"Recalculated, no change. {before}"


async def test_answer_buttons_are_available_only_while_a_question_is_open(
    hass: HomeAssistant,
) -> None:
    """A greyed-out button is the clearest signal that nothing wants an answer.

    Recalculate stays available throughout: reconsidering is meaningful at any
    time, and it is the way back from a rejection.
    """
    with freeze_time(EVENING):
        await _setup(hass)

        assert hass.states.get(ACCEPT).state != "unavailable"
        assert hass.states.get(REJECT).state != "unavailable"

        await _press(hass, "accept_plan")

        assert hass.states.get(ACCEPT).state == "unavailable"
        assert hass.states.get(REJECT).state == "unavailable"
        assert hass.states.get(RECALCULATE).state != "unavailable"


async def test_pressing_an_unavailable_answer_button_changes_nothing(
    hass: HomeAssistant,
) -> None:
    """Home Assistant skips unavailable entities, so a stale press is inert.

    Worth pinning down, because Phase 5 sends notification actions that call
    these same buttons and a notification can outlive the question it asked.
    """
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")
        approved = hass.states.get(APPROVED_START).state

        await _press(hass, "reject_plan")

        assert hass.states.get(STATUS).state == "approved"
        assert hass.states.get(APPROVED_START).state == approved


async def test_the_summary_asks_the_question_in_one_sentence(
    hass: HomeAssistant,
) -> None:
    """The point of the sentence: no assembling an answer from eight entities."""
    with freeze_time(EVENING):
        await _setup(hass)

        summary = hass.states.get(SUMMARY).state
        assert summary.startswith("New plan: approve charging 02:00-")
        assert "tomorrow" in summary
        assert "DKK" in summary
        assert len(summary) <= 255

        await _press(hass, "accept_plan")

        assert hass.states.get(SUMMARY).state.startswith("Charging 02:00-")


async def test_the_summary_names_the_reason_for_a_replan(
    hass: HomeAssistant,
) -> None:
    """Asking for approval without saying what moved is a question with no context."""
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")

        hass.states.async_set(SOC, "20", {"unit_of_measurement": "%"})
        await hass.async_block_till_done()

        summary = hass.states.get(SUMMARY).state
        assert summary.startswith(
            "Battery level changed: approve moving charging from 02:00-"
        )
        # The question is about the new window, not the one already approved.
        proposed = datetime.fromisoformat(hass.states.get(PROPOSED_START).state)
        assert f"to {proposed.astimezone(CPH):%H:%M}-" in summary


async def test_the_summary_says_when_planning_is_switched_off(
    hass: HomeAssistant,
) -> None:
    with freeze_time(EVENING):
        await _setup(hass)
        await _switch(hass, "turn_off")

        assert (
            hass.states.get(SUMMARY).state
            == "Smart charging is off; BitCruise is not planning anything."
        )


async def test_the_cost_sensor_is_rounded_to_currency_precision(
    hass: HomeAssistant,
) -> None:
    """Decimal keeps repeated addition exact; a dashboard does not need it."""
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")

        cost = hass.states.get("sensor.bitcruise_estimated_cost")
        assert len(cost.state.split(".")[1]) == 2
        assert cost.attributes["unit_of_measurement"] == "DKK"


async def test_the_currency_survives_the_price_entity_blinking(
    hass: HomeAssistant,
) -> None:
    """Found on real hardware: the summary lost "for 21.51 DKK" for one update.

    The currency is read live from the price entity, so an unavailable moment
    was stripping the unit off the cost — and off the cost sensor with it, which
    Home Assistant treats as a sensor changing its unit under the recorder.
    """
    with freeze_time(EVENING):
        await _setup(hass)
        await _press(hass, "accept_plan")
        assert "DKK" in hass.states.get(SUMMARY).state

        hass.states.async_set(PRICE, "unavailable")
        await hass.async_block_till_done()

        assert "DKK" in hass.states.get(SUMMARY).state
        cost = hass.states.get("sensor.bitcruise_estimated_cost")
        assert cost.attributes["unit_of_measurement"] == "DKK"
