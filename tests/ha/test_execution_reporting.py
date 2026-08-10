"""Phase 6a: the charger is read and reported on, never pressed.

The decision matrix itself is covered in ``tests/test_execution.py``, which is
pure. What matters here is the wiring: that charger entities are read and
normalized, that a charger state change wakes the coordinator, and that what
BitCruise *would* do is visible without it doing anything.
"""

from datetime import timedelta
from typing import Any

from custom_components.bitcruise.const import (
    CONF_APPROVAL_POLICY,
    CONF_AUTHORIZATION_REQUIRED_ENTITY,
    CONF_AUTHORIZE_ENTITY,
    CONF_CHARGER_ONLINE_ENTITY,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_START_ENTITY,
    CONF_STOP_ENTITY,
    DOMAIN,
)
from custom_components.bitcruise.models import ApprovalPolicy
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_approval import (
    EVENING,
    SETTINGS,
    SOURCES,
    STATUS,
    SUMMARY,
    _setup,
)

CHARGER_MODE = "sensor.charger_mode"
AUTH_REQUIRED = "binary_sensor.charger_authorization_required"
ONLINE = "binary_sensor.charger_online"

CHARGER: dict[str, Any] = {
    CONF_CHARGER_STATUS_ENTITY: CHARGER_MODE,
    CONF_AUTHORIZATION_REQUIRED_ENTITY: AUTH_REQUIRED,
    CONF_CHARGER_ONLINE_ENTITY: ONLINE,
    CONF_AUTHORIZE_ENTITY: "button.charger_authorize",
    CONF_START_ENTITY: "button.charger_resume",
    CONF_STOP_ENTITY: "button.charger_stop",
}
AUTO: dict[str, Any] = {
    **SETTINGS,
    CONF_APPROVAL_POLICY: ApprovalPolicy.AUTOMATIC.value,
}

READY = "binary_sensor.bitcruise_ready_to_charge"


def _set_charger(
    hass: HomeAssistant,
    mode: str = "disconnected",
    authorization_required: str = "off",
    online: str = "on",
) -> None:
    """Populate the charger entities, mirroring the Zaptec vocabulary."""
    hass.states.async_set(CHARGER_MODE, mode)
    hass.states.async_set(AUTH_REQUIRED, authorization_required)
    hass.states.async_set(ONLINE, online)


async def _setup_with_charger(hass: HomeAssistant, **charger: str) -> MockConfigEntry:
    """Set up an entry with the charger controls configured."""
    _set_charger(hass, **charger)
    entry = MockConfigEntry(
        domain=DOMAIN, data={**SOURCES, **CHARGER}, options=AUTO, title="BitCruise"
    )
    entry.add_to_hass(hass)
    await _setup(hass, entry=entry)
    return entry


async def test_no_charger_configured_leaves_the_signal_unavailable(
    hass: HomeAssistant,
) -> None:
    """Reporting "not ready" would imply a fault where there is no feature."""
    with freeze_time(EVENING):
        await _setup(hass)

        assert hass.states.get(READY).state == "unavailable"
        attributes = hass.states.get(STATUS).attributes
        assert attributes["next_charger_action"] == "none"
        assert attributes["charger_status"] == "unknown"


async def test_the_charger_state_is_read_and_normalized(hass: HomeAssistant) -> None:
    """Zaptec's connected_requesting is BitCruise's plain "connected"."""
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="connected_requesting")

        attributes = hass.states.get(STATUS).attributes
        assert attributes["charger_status"] == "connected"
        assert hass.states.get(READY).state == "on"


async def test_an_unplugged_car_is_visible_the_evening_before(
    hass: HomeAssistant,
) -> None:
    """The whole point of ready_to_charge: hours of warning, not minutes."""
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="disconnected")

        assert hass.states.get(READY).state == "off"


async def test_a_charger_state_change_wakes_the_coordinator(
    hass: HomeAssistant,
) -> None:
    """Charging starts when a cable is plugged in, not when a price updates."""
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="disconnected")
        assert hass.states.get(READY).state == "off"

        hass.states.async_set(CHARGER_MODE, "connected_requesting")
        await hass.async_block_till_done()

        assert hass.states.get(READY).state == "on"


async def test_an_offline_charger_is_not_ready(hass: HomeAssistant) -> None:
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="connected_requesting", online="off")

        assert hass.states.get(READY).state == "off"
        assert hass.states.get(STATUS).attributes["charger_online"] is False


async def test_nothing_is_pressed_before_the_window(hass: HomeAssistant) -> None:
    """An approved plan for 02:00 must not start the charger at 18:00."""
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="connected_requesting")

        attributes = hass.states.get(STATUS).attributes
        assert attributes["next_charger_action"] == "none"
        assert attributes["execution_blocked_by"] == "before_window"
        assert attributes["execution_healthy"] is True


async def test_inside_the_window_it_reports_what_it_would_press(
    hass: HomeAssistant,
) -> None:
    """Phase 6a decides but does not act: the action is reported, not taken."""
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="connected_requesting")

    # Move into the approved window. The price curve no longer covers now, which
    # is exactly the case where the stored approval has to carry the plan.
    inside = EVENING + timedelta(hours=9)
    with freeze_time(inside):
        hass.states.async_set(AUTH_REQUIRED, "on")
        await hass.async_block_till_done()

        attributes = hass.states.get(STATUS).attributes
        assert attributes["next_charger_action"] == "authorize"
        assert attributes["authorization_required"] is True
        assert attributes["execution_blocked_by"] == "nothing_to_do"


async def test_the_summary_says_the_car_is_not_plugged_in(
    hass: HomeAssistant,
) -> None:
    """An approved plan and no cable is the failure worth a sentence."""
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="connected_requesting")

    inside = EVENING + timedelta(hours=9)
    with freeze_time(inside):
        hass.states.async_set(CHARGER_MODE, "disconnected")
        await hass.async_block_till_done()

        summary = hass.states.get(SUMMARY).state
        assert summary.startswith("Waiting for the car to be plugged in")


async def test_the_summary_says_when_it_is_charging(hass: HomeAssistant) -> None:
    with freeze_time(EVENING):
        await _setup_with_charger(hass, mode="connected_requesting")

    inside = EVENING + timedelta(hours=9)
    with freeze_time(inside):
        hass.states.async_set(CHARGER_MODE, "connected_charging")
        await hass.async_block_till_done()

        assert hass.states.get(SUMMARY).state.startswith("Charging now,")
        assert (
            hass.states.get(STATUS).attributes["execution_blocked_by"]
            == "already_charging"
        )


async def test_a_plan_with_no_charger_control_says_to_do_it_yourself(
    hass: HomeAssistant,
) -> None:
    """Honest about being advice-only rather than silently doing nothing."""
    with freeze_time(EVENING):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**SOURCES, CONF_CHARGER_STATUS_ENTITY: CHARGER_MODE},
            options=AUTO,
            title="BitCruise",
        )
        entry.add_to_hass(hass)
        _set_charger(hass, mode="connected_requesting")
        await _setup(hass, entry=entry)

    inside = EVENING + timedelta(hours=9)
    with freeze_time(inside):
        hass.states.async_set(CHARGER_MODE, "connected_requesting", {"poke": 1})
        await hass.async_block_till_done()

        attributes = hass.states.get(STATUS).attributes
        assert attributes["execution_blocked_by"] == "no_control_configured"
        assert attributes["execution_healthy"] is False
        assert "start the charger yourself" in hass.states.get(SUMMARY).state
