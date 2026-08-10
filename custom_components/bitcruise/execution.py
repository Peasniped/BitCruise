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
from datetime import datetime, timedelta
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
    STOPPED_EARLY = "stopped_early"
    """Charging ended below target. Someone stopped it, so it stays stopped."""
    CHARGER_STATE_UNKNOWN = "charger_state_unknown"
    CHARGER_OFFLINE = "charger_offline"
    NO_CONTROL_CONFIGURED = "no_control_configured"
    PLUG_FAULT = "plug_fault"


class AttemptVerdict(StrEnum):
    """Whether the decided action may actually be carried out now."""

    ACT = "act"
    WAIT = "wait"
    """Already pressed; the charger has not had time to respond yet."""
    GIVE_UP = "give_up"
    """Pressed repeatedly and nothing changed. Something is wrong."""
    REPORT_ONLY = "report_only"
    """Execution is switched off. The decision stands; nothing is pressed."""


# A charger takes a moment to change state, and the coordinator re-evaluates on
# every source entity change — several times a second while a car is plugging
# in. Without a cooldown the same button would be pressed in a burst.
ATTEMPT_COOLDOWN = timedelta(seconds=60)

# After this many attempts with no change in charger state, stop. Charging is
# convenience automation: pressing a button that plainly does nothing, forever,
# is worse than stopping and saying so.
MAX_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class ExecutionMarker:
    """What was last attempted, so a restart cannot repeat it.

    Keyed on the plan as well as the action: the same action against a *different*
    plan is a new situation and starts its attempt count again.
    """

    plan_id: str
    action: ExecutionAction
    at: datetime
    attempts: int = 1

    def covers(self, plan_id: str, action: ExecutionAction) -> bool:
        """Whether this marker describes the same attempt being considered."""
        return self.plan_id == plan_id and self.action is action


def should_attempt(
    *,
    decision: ExecutionDecision,
    plan_id: str | None,
    marker: ExecutionMarker | None,
    now: datetime,
    execution_enabled: bool,
    cooldown: timedelta = ATTEMPT_COOLDOWN,
    max_attempts: int = MAX_ATTEMPTS,
) -> AttemptVerdict:
    """Decide whether to carry out the action, wait, or stop trying.

    Verification is by construction rather than by polling: ``next_action`` is
    recomputed from the charger's own state every evaluation, so an action that
    worked stops being decided. An action still being decided after several
    attempts is one that did not work.
    """
    if decision.action is ExecutionAction.NONE or plan_id is None:
        return AttemptVerdict.WAIT
    if not execution_enabled:
        return AttemptVerdict.REPORT_ONLY
    if marker is None or not marker.covers(plan_id, decision.action):
        return AttemptVerdict.ACT
    if marker.attempts >= max_attempts:
        return AttemptVerdict.GIVE_UP
    if to_utc(now) - to_utc(marker.at) < cooldown:
        return AttemptVerdict.WAIT
    return AttemptVerdict.ACT


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
    charge_needed: bool = True,
    resume: bool = False,
) -> ExecutionDecision:
    """Decide the single next charger action.

    ``authorization_required`` and ``charger_online`` are None when no entity
    reports them. None means "not known", never "no": an installation without an
    authorization sensor still authorizes if it has the button, because trying
    and being ignored is cheaper than never starting.

    ``resume`` overrides a respected manual stop, and is what pressing
    Recalculate means once charging has been stopped by hand.
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
        # A charger reports "finished" both when the car reached its target and
        # when a person pressed stop. It cannot tell them apart, but BitCruise
        # can: still below target, with window remaining, means someone stopped
        # it. That is respected rather than immediately undone — two planners
        # fighting over one charger is worse than a charge that did not happen
        # — but it must be recoverable, which is what ``resume`` is for.
        if charge_needed and not resume:
            return _idle(ExecutionBlocker.STOPPED_EARLY)
        if not charge_needed:
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
