"""Decide the next charger action, without taking it.

Pure Python: no Home Assistant, no entities, no service calls. The coordinator
passes in what it read this evaluation and gets back the single action that
should happen next, or the reason nothing can.

Two decisions shape this module.

**One action at a time, re-decided on every evaluation.** DESIGN.md section 9
describes authorize-then-start as a sequence, but firing a sequence blind is how
a charger gets pressed three times. Deciding only the *next* action and then
re-reading the charger state makes each step conditional on the last one having
worked, and makes the whole thing safe to re-enter after a restart.

**Never guess when the charger says nothing.** Control entities are routinely
``unavailable`` while nothing is plugged in, and an unknown charger state is not
evidence that a car is absent. Where the state cannot be read, the decision says
so rather than acting hopefully.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .models import ChargePlan, to_utc
from .source_normalization import ChargerStatus, PlugStatus


class ExecutionAction(StrEnum):
    """The next thing to do to the charger."""

    NONE = "none"
    AUTHORIZE = "authorize"
    START = "start"
    STOP = "stop"


class ExecutionBlocker(StrEnum):
    """Why no action is being taken.

    Separated from the action so a dashboard can distinguish "nothing to do
    because all is well" from "nothing to do because something is wrong". Only
    ``NOTHING_TO_DO`` and the waiting states are healthy.
    """

    NOTHING_TO_DO = "nothing_to_do"
    NO_APPROVED_PLAN = "no_approved_plan"
    SMART_CHARGING_OFF = "smart_charging_off"
    BEFORE_WINDOW = "before_window"
    AFTER_WINDOW = "after_window"
    CAR_NOT_CONNECTED = "car_not_connected"
    ALREADY_CHARGING = "already_charging"
    CHARGING_FINISHED = "charging_finished"
    CHARGER_STATE_UNKNOWN = "charger_state_unknown"
    CHARGER_OFFLINE = "charger_offline"
    NO_CONTROL_CONFIGURED = "no_control_configured"
    PLUG_FAULT = "plug_fault"


@dataclass(frozen=True, slots=True)
class ChargerCapabilities:
    """Which controls the user actually configured.

    A charger may not need all of them: some authorize automatically, some have
    no pause. An action that was never configured is not a failure, it is a
    capability this installation does not have.
    """

    can_authorize: bool = False
    can_start: bool = False
    can_stop: bool = False
    has_status: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """What should happen next, and why."""

    action: ExecutionAction
    blocker: ExecutionBlocker

    @property
    def is_healthy(self) -> bool:
        """Whether the current state is expected rather than a problem.

        Waiting for a window, or for a car to be plugged in, is not a fault.
        """
        return self.blocker in _HEALTHY_BLOCKERS


_HEALTHY_BLOCKERS = frozenset(
    {
        ExecutionBlocker.NOTHING_TO_DO,
        ExecutionBlocker.NO_APPROVED_PLAN,
        ExecutionBlocker.SMART_CHARGING_OFF,
        ExecutionBlocker.BEFORE_WINDOW,
        ExecutionBlocker.AFTER_WINDOW,
        ExecutionBlocker.ALREADY_CHARGING,
        ExecutionBlocker.CHARGING_FINISHED,
    }
)


def _idle(blocker: ExecutionBlocker) -> ExecutionDecision:
    """Build a decision that takes no action."""
    return ExecutionDecision(action=ExecutionAction.NONE, blocker=blocker)


def _act(action: ExecutionAction) -> ExecutionDecision:
    """Build a decision that acts."""
    return ExecutionDecision(action=action, blocker=ExecutionBlocker.NOTHING_TO_DO)


def next_action(
    *,
    now: datetime,
    approved: ChargePlan | None,
    charger: ChargerStatus,
    capabilities: ChargerCapabilities,
    smart_charging: bool = True,
    authorization_required: bool | None = None,
    charger_online: bool | None = None,
    plug: PlugStatus = PlugStatus.UNKNOWN,
) -> ExecutionDecision:
    """Decide the single next charger action.

    ``authorization_required`` and ``charger_online`` are None when no entity
    reports them. None means "not known", never "no": an installation without an
    authorization sensor still authorizes if it has the button, because trying
    and being ignored is cheaper than never starting.
    """
    if not smart_charging:
        return _idle(ExecutionBlocker.SMART_CHARGING_OFF)

    if approved is None or not approved.has_window:
        return _idle(ExecutionBlocker.NO_APPROVED_PLAN)

    reference = to_utc(now)
    inside = to_utc(approved.start) <= reference < to_utc(approved.end)

    if reference >= to_utc(approved.end):
        # The window is over. Stopping is the one action still worth taking,
        # and only if the charger is demonstrably still delivering energy.
        if charger is ChargerStatus.CHARGING and capabilities.can_stop:
            return _act(ExecutionAction.STOP)
        return _idle(ExecutionBlocker.AFTER_WINDOW)

    if not inside:
        return _idle(ExecutionBlocker.BEFORE_WINDOW)

    if charger_online is False:
        return _idle(ExecutionBlocker.CHARGER_OFFLINE)

    if charger is ChargerStatus.CHARGING:
        return _idle(ExecutionBlocker.ALREADY_CHARGING)
    if charger is ChargerStatus.FINISHED:
        return _idle(ExecutionBlocker.CHARGING_FINISHED)
    if charger is ChargerStatus.DISCONNECTED:
        return _idle(ExecutionBlocker.CAR_NOT_CONNECTED)

    if charger is ChargerStatus.UNKNOWN:
        # No status entity configured is a different situation from one that is
        # configured and unreadable, but both leave us unable to confirm a car
        # is present. The vehicle's own plug sensor can still answer it.
        if plug is PlugStatus.FAULT:
            return _idle(ExecutionBlocker.PLUG_FAULT)
        if plug is PlugStatus.DISCONNECTED:
            return _idle(ExecutionBlocker.CAR_NOT_CONNECTED)
        if plug is not PlugStatus.CONNECTED:
            return _idle(ExecutionBlocker.CHARGER_STATE_UNKNOWN)

    # Plugged in, inside the window, and not yet charging.
    if authorization_required is not False and capabilities.can_authorize:
        return _act(ExecutionAction.AUTHORIZE)
    if capabilities.can_start:
        return _act(ExecutionAction.START)
    return _idle(ExecutionBlocker.NO_CONTROL_CONFIGURED)


def ready_to_charge(
    *,
    charger: ChargerStatus,
    capabilities: ChargerCapabilities,
    charger_online: bool | None = None,
    plug: PlugStatus = PlugStatus.UNKNOWN,
) -> bool | None:
    """Whether charging could begin if a window opened right now.

    Deliberately independent of the plan and the clock: it answers "is the
    hardware ready", so a car left unplugged at bedtime is visible hours before
    the window it would have missed. None when nothing can say.
    """
    if not capabilities.can_start:
        return None
    if charger_online is False:
        return False
    if charger.is_plugged_in:
        return True
    if charger is ChargerStatus.DISCONNECTED:
        return False
    if plug is PlugStatus.CONNECTED:
        return True
    if plug is PlugStatus.DISCONNECTED or plug is PlugStatus.FAULT:
        return False
    return None
