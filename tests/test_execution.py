"""Tests for the charger execution decision.

Phase 6a decides but never acts, so every case here is about what *would*
happen. The cases that matter are the ones where acting would be wrong: outside
the window, no car, a charger that has not said anything, and a control the user
never configured.
"""

from datetime import timedelta

import pytest
from custom_components.bitcruise.execution import (
    AttemptVerdict,
    ChargerCapabilities,
    ExecutionAction,
    ExecutionBlocker,
    ExecutionDecision,
    ExecutionMarker,
    next_action,
    ready_to_charge,
    should_attempt,
)
from custom_components.bitcruise.models import ChargePlan, ChargeUrgency
from custom_components.bitcruise.source_normalization import ChargerStatus, PlugStatus

from .builders import advance, at

FULL = ChargerCapabilities(
    can_authorize=True, can_start=True, can_stop=True, has_status=True
)
START_ONLY = ChargerCapabilities(can_start=True)
NO_CONTROLS = ChargerCapabilities(has_status=True)


def window(start_hour: int = 2, end_hour: int = 6) -> ChargePlan:
    """Build a plan booking a window on 10 August."""
    return ChargePlan(
        id="test",
        created_at=at(0),
        start=at(start_hour),
        end=at(end_hour),
        current_soc_pct=50.0,
        target_soc_pct=80.0,
        reserve_floor_pct=0.0,
        required_battery_kwh=20.0,
        required_grid_kwh=22.0,
        planned_grid_kwh=22.0,
        allocated_grid_kwh=22.0,
        estimated_soc_at_end=80.0,
        can_meet_target=True,
        shortfall_kwh=0.0,
        estimated_cost=None,
        price_quality=None,
        urgency=ChargeUrgency.NORMAL,
        below_reserve_floor=False,
    )


def decide(**overrides: object):
    """Run one decision with sensible defaults: inside the window, plugged in."""
    kwargs: dict[str, object] = {
        "now": at(3),
        "approved": window(),
        "charger": ChargerStatus.CONNECTED,
        "capabilities": FULL,
    }
    kwargs.update(overrides)
    return next_action(**kwargs)  # type: ignore[arg-type]


class TestTheHappyPath:
    """Authorize, then start, one step at a time."""

    def test_authorize_comes_first_when_it_is_required(self):
        assert decide(authorization_required=True).action is ExecutionAction.AUTHORIZE

    def test_start_follows_once_authorization_is_no_longer_required(self):
        """The second evaluation, after the charger accepted the authorization."""
        assert decide(authorization_required=False).action is ExecutionAction.START

    def test_authorize_is_attempted_when_nothing_reports_whether_it_is_needed(self):
        """Trying and being ignored is cheaper than never starting."""
        assert decide(authorization_required=None).action is ExecutionAction.AUTHORIZE

    def test_start_is_the_only_step_when_no_authorize_control_exists(self):
        assert (
            decide(capabilities=START_ONLY, authorization_required=None).action
            is ExecutionAction.START
        )

    def test_nothing_happens_once_charging(self):
        decision = decide(charger=ChargerStatus.CHARGING)
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.ALREADY_CHARGING
        assert decision.is_healthy


class TestTheClock:
    """A window is a permission slip, not a suggestion."""

    def test_nothing_before_the_window(self):
        decision = decide(now=at(1))
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.BEFORE_WINDOW

    def test_the_window_start_is_inclusive(self):
        assert decide(now=at(2), authorization_required=False).action is (
            ExecutionAction.START
        )

    def test_the_window_end_is_exclusive(self):
        """At exactly the end the window is over, so nothing may start."""
        assert decide(now=at(6)).action is not ExecutionAction.START

    def test_charging_past_the_end_is_stopped(self):
        decision = decide(now=at(6), charger=ChargerStatus.CHARGING)
        assert decision.action is ExecutionAction.STOP

    def test_nothing_to_stop_after_the_end_when_not_charging(self):
        decision = decide(now=at(7), charger=ChargerStatus.CONNECTED)
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.AFTER_WINDOW

    def test_a_charger_that_cannot_stop_is_not_asked_to(self):
        decision = decide(
            now=at(7), charger=ChargerStatus.CHARGING, capabilities=START_ONLY
        )
        assert decision.action is ExecutionAction.NONE

    def test_dst_is_measured_in_real_elapsed_time(self):
        """Wall-clock comparison would put this an hour off, twice a year."""
        plan = window()
        just_inside = plan.start + timedelta(minutes=1)
        assert decide(now=just_inside, authorization_required=False).action is (
            ExecutionAction.START
        )


class TestNothingToActOn:
    """States where acting would be wrong, not merely unnecessary."""

    def test_no_approved_plan(self):
        decision = decide(approved=None)
        assert decision.blocker is ExecutionBlocker.NO_APPROVED_PLAN
        assert decision.is_healthy

    def test_smart_charging_off_outranks_everything(self):
        decision = decide(smart_charging=False, charger=ChargerStatus.CONNECTED)
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.SMART_CHARGING_OFF

    def test_no_car_connected(self):
        decision = decide(charger=ChargerStatus.DISCONNECTED)
        assert decision.blocker is ExecutionBlocker.CAR_NOT_CONNECTED

    def test_an_offline_charger_is_not_pressed(self):
        decision = decide(charger_online=False)
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.CHARGER_OFFLINE
        assert not decision.is_healthy

    def test_a_finished_session_is_not_restarted(self):
        decision = decide(charger=ChargerStatus.FINISHED)
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.CHARGING_FINISHED

    def test_configured_nothing_means_nothing_can_be_done(self):
        decision = decide(capabilities=NO_CONTROLS)
        assert decision.blocker is ExecutionBlocker.NO_CONTROL_CONFIGURED
        assert not decision.is_healthy


class TestUnknownChargerState:
    """The charger said nothing. The car may still be able to answer."""

    def test_the_vehicle_plug_sensor_is_used_as_a_fallback(self):
        decision = decide(
            charger=ChargerStatus.UNKNOWN,
            plug=PlugStatus.CONNECTED,
            authorization_required=False,
        )
        assert decision.action is ExecutionAction.START

    def test_a_disconnected_car_is_believed(self):
        decision = decide(charger=ChargerStatus.UNKNOWN, plug=PlugStatus.DISCONNECTED)
        assert decision.blocker is ExecutionBlocker.CAR_NOT_CONNECTED

    def test_a_plug_fault_is_not_hidden_as_not_connected(self):
        decision = decide(charger=ChargerStatus.UNKNOWN, plug=PlugStatus.FAULT)
        assert decision.blocker is ExecutionBlocker.PLUG_FAULT
        assert not decision.is_healthy

    def test_neither_source_knowing_anything_acts_on_nothing(self):
        """An unreadable charger is not evidence that a car is present."""
        decision = decide(charger=ChargerStatus.UNKNOWN, plug=PlugStatus.UNKNOWN)
        assert decision.action is ExecutionAction.NONE
        assert decision.blocker is ExecutionBlocker.CHARGER_STATE_UNKNOWN
        assert not decision.is_healthy


class TestShouldAttempt:
    """When a decided action may actually be carried out.

    The decision itself says *what*; this says *whether now*. Everything here
    exists to stop a real button being pressed in a burst, or forever.
    """

    START = ExecutionDecision(
        action=ExecutionAction.START, blocker=ExecutionBlocker.NOTHING_TO_DO
    )
    IDLE = ExecutionDecision(
        action=ExecutionAction.NONE, blocker=ExecutionBlocker.BEFORE_WINDOW
    )

    def verdict(self, **overrides: object) -> AttemptVerdict:
        """Run one attempt decision, defaulting to a fresh start action."""
        kwargs: dict[str, object] = {
            "decision": self.START,
            "plan_id": "plan-1",
            "marker": None,
            "now": at(3),
            "execution_enabled": True,
        }
        kwargs.update(overrides)
        return should_attempt(**kwargs)  # type: ignore[arg-type]

    def test_a_fresh_action_is_carried_out(self):
        assert self.verdict() is AttemptVerdict.ACT

    def test_nothing_to_do_is_not_an_attempt(self):
        assert self.verdict(decision=self.IDLE) is AttemptVerdict.WAIT

    def test_execution_switched_off_reports_without_pressing(self):
        """The dry run: BitCruise decides exactly as it would, and acts on none."""
        assert self.verdict(execution_enabled=False) is AttemptVerdict.REPORT_ONLY

    def test_a_repeat_within_the_cooldown_waits(self):
        """The coordinator re-evaluates several times a second while plugging in."""
        marker = ExecutionMarker("plan-1", ExecutionAction.START, at=at(3))
        pressed_again = advance(at(3), timedelta(seconds=10))
        assert self.verdict(marker=marker, now=pressed_again) is AttemptVerdict.WAIT

    def test_a_repeat_after_the_cooldown_tries_again(self):
        marker = ExecutionMarker("plan-1", ExecutionAction.START, at=at(3))
        later = advance(at(3), timedelta(minutes=2))
        assert self.verdict(marker=marker, now=later) is AttemptVerdict.ACT

    def test_it_stops_after_repeated_failures(self):
        """A button that plainly does nothing must not be pressed forever."""
        marker = ExecutionMarker("plan-1", ExecutionAction.START, at=at(3), attempts=3)
        later = advance(at(3), timedelta(hours=1))
        assert self.verdict(marker=marker, now=later) is AttemptVerdict.GIVE_UP

    def test_a_different_action_starts_its_own_count(self):
        """Authorize having failed says nothing about whether stop will."""
        marker = ExecutionMarker(
            "plan-1", ExecutionAction.AUTHORIZE, at=at(3), attempts=3
        )
        assert self.verdict(marker=marker) is AttemptVerdict.ACT

    def test_a_different_plan_starts_its_own_count(self):
        marker = ExecutionMarker("plan-0", ExecutionAction.START, at=at(3), attempts=3)
        assert self.verdict(marker=marker) is AttemptVerdict.ACT

    def test_a_marker_from_before_a_restart_still_counts(self):
        """Idempotency across a restart: the marker is persisted, so it holds."""
        marker = ExecutionMarker("plan-1", ExecutionAction.START, at=at(3), attempts=1)
        just_after = advance(at(3), timedelta(seconds=5))
        assert self.verdict(marker=marker, now=just_after) is AttemptVerdict.WAIT


class TestReadyToCharge:
    """Hardware readiness, independent of the plan and the clock."""

    @pytest.mark.parametrize(
        ("charger", "expected"),
        [
            (ChargerStatus.CONNECTED, True),
            (ChargerStatus.CHARGING, True),
            (ChargerStatus.FINISHED, True),
            (ChargerStatus.DISCONNECTED, False),
        ],
    )
    def test_follows_the_charger_when_it_reports(
        self, charger: ChargerStatus, expected: bool
    ):
        assert ready_to_charge(charger=charger, capabilities=FULL) is expected

    def test_falls_back_to_the_vehicle_plug_sensor(self):
        assert (
            ready_to_charge(
                charger=ChargerStatus.UNKNOWN,
                capabilities=FULL,
                plug=PlugStatus.CONNECTED,
            )
            is True
        )

    def test_unknown_when_nothing_reports(self):
        assert ready_to_charge(charger=ChargerStatus.UNKNOWN, capabilities=FULL) is None

    def test_unknown_when_no_charger_is_configured_at_all(self):
        """Reporting "not ready" would imply a fault where there is no feature."""
        assert (
            ready_to_charge(
                charger=ChargerStatus.CONNECTED, capabilities=ChargerCapabilities()
            )
            is None
        )

    def test_an_offline_charger_is_not_ready(self):
        assert (
            ready_to_charge(
                charger=ChargerStatus.CONNECTED,
                capabilities=FULL,
                charger_online=False,
            )
            is False
        )
