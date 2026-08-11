"""Phase 6b: BitCruise operates the charger.

Every test here counts service calls, because the failure that matters is not a
wrong answer on a dashboard — it is a real button pressed when it should not
have been, or pressed twice. Nothing here talks to a real charger.

The mock services are installed *after* setup, deliberately. Forwarding the
config entry to the button platform sets up the ``button`` component, which
registers the real ``button.press`` and would replace a mock installed earlier.
"""

from datetime import timedelta
from typing import Any

from custom_components.bitcruise.const import (
    CONF_AUTHORIZE_ENTITY,
    CONF_START_ENTITY,
    DOMAIN,
)
from freezegun import freeze_time
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from .test_approval import EVENING, SOURCES, STATUS, SUMMARY, _setup
from .test_execution_reporting import (
    AUTH_REQUIRED,
    AUTO,
    CHARGER,
    CHARGER_MODE,
    _set_charger,
)

CONTROL = "switch.bitcruise_operate_the_charger"
AUTHORIZE_BUTTON = "button.charger_authorize"
START_BUTTON = "button.charger_resume"
CHARGING_SWITCH = "switch.charger_charging"
SOC_AT_READY = "sensor.bitcruise_estimated_soc_at_ready"
UNREACHABLE = "binary_sensor.bitcruise_charge_shortfall"

# Nine hours past the 18:00 setup puts the clock inside the approved window.
INSIDE = EVENING + timedelta(hours=9)


async def _arrive(
    hass: HomeAssistant, charger: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up in the evening with the car away and the charger needing auth."""
    _set_charger(hass, mode="disconnected", authorization_required="on")
    # The controls themselves. A charger integration provides these; without
    # them BitCruise correctly declines to call anything.
    hass.states.async_set(AUTHORIZE_BUTTON, "unknown")
    hass.states.async_set(START_BUTTON, "unknown")
    hass.states.async_set(CHARGING_SWITCH, "off")

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**SOURCES, **(charger if charger is not None else CHARGER)},
        options=AUTO,
        title="BitCruise",
    )
    entry.add_to_hass(hass)
    with freeze_time(EVENING):
        await _setup(hass, entry=entry)
    return entry


def _watch(hass: HomeAssistant) -> list[ServiceCall]:
    """Record button presses from now on instead of performing them."""
    return async_mock_service(hass, "button", "press")


async def _plug_in(hass: HomeAssistant, mode: str = "connected_requesting") -> None:
    """Plug the car in."""
    hass.states.async_set(CHARGER_MODE, mode)
    await hass.async_block_till_done()


async def _enable(hass: HomeAssistant) -> None:
    """Let BitCruise operate the charger."""
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: CONTROL}, blocking=True
    )
    await hass.async_block_till_done()


def _pressed(calls: list[ServiceCall]) -> list[str]:
    """Return the entity ids that were operated, in order."""
    targets: list[str] = []
    for call in calls:
        target = call.data[ATTR_ENTITY_ID]
        targets.extend([target] if isinstance(target, str) else target)
    return targets


async def test_nothing_is_pressed_while_execution_is_off(
    hass: HomeAssistant,
) -> None:
    """The default. Everything is decided and reported; nothing is touched."""
    await _arrive(hass)
    presses = _watch(hass)
    with freeze_time(INSIDE):
        await _plug_in(hass)

        assert presses == []
        assert hass.states.get(STATUS).attributes["next_charger_action"] == "authorize"
        assert hass.states.get(STATUS).attributes["execution_enabled"] is False
        # Found on real hardware: this used to read "Charging 15:18-20:00
        # today" while the charger sat waiting to be authorized.
        assert hass.states.get(SUMMARY).state.startswith(
            "The charger needs authorizing yourself"
        )


async def test_a_pending_action_does_not_claim_to_be_charging(
    hass: HomeAssistant,
) -> None:
    """The window is open and the cable is in; energy is not flowing yet."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        _watch(hass)
        await _plug_in(hass)

        summary = hass.states.get(SUMMARY).state
        assert summary.startswith("Authorizing the charger for")
        assert "Charging" not in summary


async def test_enabling_execution_presses_the_decided_button(
    hass: HomeAssistant,
) -> None:
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)

        assert _pressed(presses) == [AUTHORIZE_BUTTON]


async def test_authorize_then_start_across_two_evaluations(
    hass: HomeAssistant,
) -> None:
    """One action at a time, each conditional on the last having worked."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)
        assert _pressed(presses) == [AUTHORIZE_BUTTON]

        # The charger accepted the authorization.
        hass.states.async_set(AUTH_REQUIRED, "off")
        await hass.async_block_till_done()

        assert _pressed(presses) == [AUTHORIZE_BUTTON, START_BUTTON]


async def test_a_burst_of_updates_presses_once(hass: HomeAssistant) -> None:
    """Plugging a car in produces several state changes in a second."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)

        for index in range(5):
            hass.states.async_set(CHARGER_MODE, "connected_requesting", {"n": index})
            await hass.async_block_till_done()

        assert len(presses) == 1


async def test_it_stops_pressing_a_button_that_does_nothing(
    hass: HomeAssistant,
) -> None:
    """A charger ignoring us is reported, not hammered."""
    await _arrive(hass)
    with freeze_time(INSIDE) as clock:
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)

        for index in range(6):
            clock.tick(timedelta(minutes=5))
            hass.states.async_set(CHARGER_MODE, "connected_requesting", {"t": index})
            await hass.async_block_till_done()

        assert len(presses) == 3
        attributes = hass.states.get(STATUS).attributes
        assert attributes["last_action_attempts"] == 3
        assert attributes["execution_stalled"] is True
        assert hass.states.get(SUMMARY).state.startswith(
            "The charger is not responding"
        )


async def test_charging_stops_nothing_further(hass: HomeAssistant) -> None:
    """Once the charger reports charging, there is nothing left to decide."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)
        before = len(presses)

        hass.states.async_set(CHARGER_MODE, "connected_charging")
        await hass.async_block_till_done()

        assert len(presses) == before
        assert hass.states.get(STATUS).attributes["next_charger_action"] == "none"


async def test_an_unavailable_control_is_not_called(hass: HomeAssistant) -> None:
    """Unavailable means "cannot act yet" while unplugged, not "failed"."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        hass.states.async_set(AUTHORIZE_BUTTON, "unavailable")
        await _plug_in(hass)

        assert presses == []


async def test_an_untried_control_does_not_burn_an_attempt(
    hass: HomeAssistant,
) -> None:
    """Otherwise a charger that comes back finds its retries already spent."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        hass.states.async_set(AUTHORIZE_BUTTON, "unavailable")
        await _plug_in(hass)
        assert hass.states.get(STATUS).attributes["last_action_attempts"] == 0

        hass.states.async_set(AUTHORIZE_BUTTON, "unknown")
        await hass.async_block_till_done()

        assert _pressed(presses) == [AUTHORIZE_BUTTON]


async def test_turning_execution_off_again_stops_acting(
    hass: HomeAssistant,
) -> None:
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)
        assert len(presses) == 1

        await hass.services.async_call(
            "switch", "turn_off", {ATTR_ENTITY_ID: CONTROL}, blocking=True
        )
        await hass.async_block_till_done()
        hass.states.async_set(CHARGER_MODE, "connected_requesting", {"again": 1})
        await hass.async_block_till_done()

        assert len(presses) == 1


async def test_a_restart_mid_session_does_not_press_again(
    hass: HomeAssistant,
) -> None:
    """The idempotency marker is persisted precisely for this."""
    entry = await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)
        assert len(presses) == 1

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await _setup(hass, entry=entry)
        presses = _watch(hass)
        await hass.async_block_till_done()

        assert presses == []


async def test_a_manual_stop_is_respected_and_recoverable(
    hass: HomeAssistant,
) -> None:
    """Reported on real hardware: stopping by hand left no way back.

    The charger reports "finished" whether the target was reached or someone
    pressed stop. Below target it is a manual stop, which is respected — but
    Recalculate has to be a way out, or only unplugging the cable clears it.
    """
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)
        assert len(presses) == 1

        # Someone presses stop on the charger. The car is still below target.
        hass.states.async_set(CHARGER_MODE, "connected_finished")
        await hass.async_block_till_done()

        assert hass.states.get(STATUS).attributes["execution_blocked_by"] == (
            "stopped_early"
        )
        assert hass.states.get(SUMMARY).state.startswith(
            "Charging was stopped prematurely"
        )
        assert len(presses) == 1, "a manual stop must not be immediately undone"

        await hass.services.async_call(
            "button",
            "press",
            {ATTR_ENTITY_ID: "button.bitcruise_recalculate_plan"},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert len(presses) > 1, "Recalculate must be a way back"


async def test_reconnecting_clears_a_manual_stop(hass: HomeAssistant) -> None:
    """Driving off and coming back is the other way out, and the common one."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        presses = _watch(hass)
        await _plug_in(hass)
        hass.states.async_set(CHARGER_MODE, "connected_finished")
        await hass.async_block_till_done()
        stopped = len(presses)

        await _plug_in(hass, mode="disconnected")
        await _plug_in(hass)

        assert len(presses) > stopped


async def test_a_late_start_corrects_the_reachable_charge(
    hass: HomeAssistant,
) -> None:
    """A window booked for four hours that only gets one delivers a quarter.

    The plan was costed for the whole window, so every figure derived from it
    promises more than the car will have. The window is not extended to make up
    for it — that would be moving an approved plan — so the honest thing to move
    is the expectation.
    """
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        _watch(hass)
        await _plug_in(hass)
        planned = float(hass.states.get(SOC_AT_READY).state)

    # Three hours into a 03:00-07:00 window, charging finally begins.
    with freeze_time(INSIDE + timedelta(hours=3)):
        hass.states.async_set(CHARGER_MODE, "connected_charging")
        await hass.async_block_till_done()

        reachable = float(hass.states.get(SOC_AT_READY).state)
        assert reachable < planned, "one hour cannot deliver what four were costed for"
        assert hass.states.get(UNREACHABLE).state == "on"


async def test_starting_on_time_leaves_the_estimate_alone(
    hass: HomeAssistant,
) -> None:
    """Only a genuinely late start is corrected; a prompt one is not."""
    await _arrive(hass)
    with freeze_time(INSIDE):
        await _enable(hass)
        _watch(hass)
        await _plug_in(hass)
        planned = hass.states.get(SOC_AT_READY).state

        hass.states.async_set(CHARGER_MODE, "connected_charging")
        await hass.async_block_till_done()

        assert hass.states.get(SOC_AT_READY).state == planned


async def test_the_switch_is_unavailable_without_a_charger(
    hass: HomeAssistant,
) -> None:
    """Offering to operate a charger nobody configured would be a lie."""
    with freeze_time(EVENING):
        await _setup(hass)

        assert hass.states.get(CONTROL).state == "unavailable"


async def test_a_switch_controlled_charger_is_turned_on(hass: HomeAssistant) -> None:
    """Some chargers expose a switch rather than buttons."""
    charger: dict[str, Any] = {
        key: value
        for key, value in CHARGER.items()
        if key != CONF_AUTHORIZE_ENTITY  # this one authorizes by itself
    }
    charger[CONF_START_ENTITY] = CHARGING_SWITCH
    await _arrive(hass, charger)

    with freeze_time(INSIDE):
        # Enable first: mocking switch.turn_on would otherwise stop the
        # BitCruise switch itself from turning on.
        await _enable(hass)
        hass.states.async_set(AUTH_REQUIRED, "off")
        switch_calls = async_mock_service(hass, "switch", "turn_on")
        presses = _watch(hass)
        await _plug_in(hass)

        assert _pressed(switch_calls) == [CHARGING_SWITCH]
        assert presses == []
