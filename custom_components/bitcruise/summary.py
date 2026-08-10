"""One sentence describing what BitCruise is doing.

Pure Python: no Home Assistant, no entities. It takes the figures an evaluation
already produced and renders the sentence a person reads first, before deciding
whether any of the other entities are worth looking at.

Two constraints shape it. A Home Assistant state may not exceed 255 characters,
so the result is clipped rather than allowed to take the entity offline. And the
sentence answers one question per lifecycle state — a state that cannot say
anything useful says so plainly instead of padding.

The wording lives here in English rather than in ``strings.json``. Home Assistant
translates *enumerated* entity states, not composed ones, so there is no
mechanism to translate a sentence with numbers in it. Keeping the phrasing in one
module means there is a single place to change when one appears.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .execution import ExecutionAction, ExecutionBlocker
from .models import ChargePlan, ChargeRequirement, PlanSource, PlanStatus, to_utc

# Home Assistant refuses a state longer than this.
MAX_STATE_LENGTH = 255

# Why the user is being asked, phrased as the opening of a sentence. Without
# this, an approval request is a window with no explanation of what moved it.
_REASONS: dict[PlanSource, str] = {
    PlanSource.INITIAL: "New plan",
    PlanSource.PRICE_UPDATE: "Prices changed",
    PlanSource.SOC_CHANGE: "Battery level changed",
    PlanSource.SETTINGS_CHANGE: "Settings changed",
    PlanSource.MANUAL: "Recalculated",
    PlanSource.SCHEDULE: "Time moved on",
}


# How an approved plan reads once execution has an opinion about it. Only the
# states that change what a person should do get their own sentence; the rest
# fall through to the plain "Charging ..." form, because "waiting for the window
# to open" is what an approved plan already means.
_EXECUTION: dict[ExecutionBlocker, str] = {
    ExecutionBlocker.ALREADY_CHARGING: "Charging now, {window}.",
    ExecutionBlocker.CHARGING_FINISHED: "Finished charging. Window was {window}.",
    ExecutionBlocker.STOPPED_EARLY: (
        "Charging was stopped prematurely. Press Recalculate to start again, "
        "or reconnect the cable. Window {window}."
    ),
    ExecutionBlocker.AFTER_WINDOW: "Charging window {window} has ended.",
    ExecutionBlocker.CAR_NOT_CONNECTED: (
        "Waiting for the car to be plugged in: {window}."
    ),
    ExecutionBlocker.CHARGER_OFFLINE: "Charger is offline. Window {window}.",
    ExecutionBlocker.PLUG_FAULT: "The charging cable reports a fault. Window {window}.",
    ExecutionBlocker.CHARGER_STATE_UNKNOWN: (
        "Cannot tell whether the car is plugged in. Window {window}."
    ),
    ExecutionBlocker.NO_CONTROL_CONFIGURED: (
        "Charging {window} — start the charger yourself, none is configured."
    ),
}


@dataclass(frozen=True, slots=True)
class _PendingPhrase:
    """How a not-yet-taken action reads, acting and merely reporting."""

    acting: str
    waiting: str


# What is about to happen, for the gap between a window opening and energy
# actually flowing. Which half is used depends on whether BitCruise is allowed
# to operate the charger.
_PENDING_ACTION: dict[ExecutionAction, _PendingPhrase] = {
    ExecutionAction.AUTHORIZE: _PendingPhrase(
        acting="Authorizing the charger for", waiting="The charger needs authorizing"
    ),
    ExecutionAction.START: _PendingPhrase(
        acting="Starting the charger for", waiting="The charger needs starting"
    ),
    ExecutionAction.STOP: _PendingPhrase(
        acting="Stopping the charger after", waiting="The charger needs stopping"
    ),
}


def _clip(text: str) -> str:
    """Keep a sentence inside the state length limit."""
    if len(text) <= MAX_STATE_LENGTH:
        return text
    return text[: MAX_STATE_LENGTH - 1].rstrip() + "…"


def _day_label(moment: datetime, now: datetime) -> str:
    """Name the day a window starts on, relative to now.

    Deliberately not "tonight": a window starting at 02:00 is tonight in casual
    speech and tomorrow on the calendar, and the calendar is the one that cannot
    be misread.
    """
    days = (moment.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return moment.strftime("%a %d %b")


def _window(plan: ChargePlan, now: datetime) -> str:
    """Render a plan's window in the same timezone as ``now``.

    The day qualifier describes the start. A window crossing midnight is common
    and reads correctly: "23:00-03:00 today" starts today, as stated.
    """
    start = plan.start.astimezone(now.tzinfo)
    end = plan.end.astimezone(now.tzinfo)
    return f"{start:%H:%M}-{end:%H:%M} {_day_label(start, now)}"


def _energy(kwh: float) -> str:
    """Render an energy figure at the precision a person needs."""
    return f"{kwh:.1f} kWh"


def _cost(amount: Decimal | None, currency: str | None) -> str:
    """Render the cost clause, or nothing when the price source gave no currency.

    A bare number with no unit invites the reader to supply their own, so the
    clause is dropped entirely rather than shown unlabelled.
    """
    if amount is None or not currency:
        return ""
    return f" for {amount:.2f} {currency}"


def _charge_clause(plan: ChargePlan, now: datetime, currency: str | None) -> str:
    """Render the window, energy and cost shared by every state with a plan."""
    return (
        f"{_window(plan, now)}, {_energy(plan.planned_grid_kwh)}"
        f"{_cost(plan.estimated_cost, currency)}"
    )


def _problem_clause(problems: Sequence[str]) -> str:
    """Name the first problem, counting the rest.

    "Error" on its own sends the user to the attributes to find out what broke.
    The first problem is usually the cause of the others.
    """
    if not problems:
        return "Cannot plan: no usable input."
    first = problems[0]
    if len(problems) == 1:
        return f"Cannot plan: {first}."
    return f"Cannot plan: {first} (+{len(problems) - 1} more)."


def _needs_charge_clause(
    *,
    plan: ChargePlan | None,
    requirement: ChargeRequirement | None,
    now: datetime,
    ready_by: datetime | None,
    currency: str | None,
) -> str:
    """Explain why charging is needed but nothing is scheduled.

    Three different situations look identical on the other entities: no prices
    yet, prices but no window that fits, and a window nobody has approved.
    """
    needed = _energy(requirement.grid_energy_required_kwh) if requirement else "energy"
    if plan is None:
        return f"Needs {needed} from the grid; waiting for electricity prices."
    if not plan.has_window:
        if ready_by is not None and to_utc(ready_by) > to_utc(now):
            deadline = ready_by.astimezone(now.tzinfo)
            return (
                f"Needs {needed} from the grid; no window fits before "
                f"{deadline:%H:%M} {_day_label(deadline, now)}."
            )
        return f"Needs {needed} from the grid; no charging window found."
    return (
        f"Needs {needed} from the grid; "
        f"{_charge_clause(plan, now, currency)} is not approved."
    )


def summarize(
    *,
    status: PlanStatus,
    now: datetime,
    plan: ChargePlan | None = None,
    requirement: ChargeRequirement | None = None,
    problems: Sequence[str] = (),
    currency: str | None = None,
    smart_charging: bool = True,
    replaces: ChargePlan | None = None,
    proposal_reason: PlanSource | None = None,
    ready_by: datetime | None = None,
    current_soc_pct: float | None = None,
    target_soc_pct: float | None = None,
    recalculated: bool = False,
    blocker: ExecutionBlocker | None = None,
    action: ExecutionAction | None = None,
    execution_enabled: bool = True,
    stalled: bool = False,
) -> str:
    """Describe the current state in one sentence.

    ``plan`` is the plan the sentence is about. While a proposal is pending that
    is the *proposal*, not the approved plan: the question being asked is about
    the new window. ``replaces`` carries the approved plan it would displace, so
    the sentence can name both ends of the move.

    ``recalculated`` marks the one evaluation a press of Recalculate produced.
    A recalculation that finds the same plan changes nothing anywhere, which
    reads as a dead button; saying so is the only feedback there is.

    ``blocker`` refines an approved plan with what execution makes of it — "the
    car is not plugged in" is the difference between a plan that will happen and
    one that will not.
    """
    if status is PlanStatus.ERROR:
        return _clip(_problem_clause(problems))

    if not smart_charging:
        return "Smart charging is off; BitCruise is not planning anything."

    # A recalculation that produced a question announces itself through the
    # proposal reason instead, so it is not prefixed twice.
    prefix = "Recalculated, no change. " if recalculated else ""

    if status is PlanStatus.AWAITING_APPROVAL and plan is not None:
        opening = _REASONS.get(proposal_reason, "Plan changed")
        clause = _charge_clause(plan, now, currency)
        if replaces is not None and replaces.has_window:
            # Naming both ends is the whole content of the question. "Approve
            # moving charging to 14:00" without saying what it moves *from*
            # cannot be answered without going and looking it up.
            return _clip(
                f"{opening}: approve moving charging from "
                f"{_window(replaces, now)} to {clause}."
            )
        return _clip(f"{opening}: approve charging {clause}.")

    if status is PlanStatus.APPROVED and plan is not None:
        clause = _charge_clause(plan, now, currency)
        if stalled:
            # The charger was asked repeatedly and did not respond. Silence
            # here would read as "charging is under way".
            #
            # The cause is named because it is nearly always the same one and
            # is invisible from outside: a charger running its own schedule
            # accepts the command and then ignores it. Nothing in the entity
            # states says so, so a user with no hint has nowhere to start.
            return _clip(
                f"{prefix}The charger is not responding — check it is not "
                f"running its own schedule. Window {_window(plan, now)}."
            )
        if blocker not in _EXECUTION and action not in (None, ExecutionAction.NONE):
            # The window is open and the car is plugged in, but charging has not
            # started: something still has to happen first. Saying "Charging"
            # here would be a plain lie, and it is the state a user is most
            # likely to be looking at the dashboard during.
            pending = _PENDING_ACTION[action]
            if not execution_enabled:
                return _clip(
                    f"{prefix}{pending.waiting} yourself — BitCruise is not "
                    f"operating the charger. Window {_window(plan, now)}."
                )
            return _clip(f"{prefix}{pending.acting} {clause}.")

        template = _EXECUTION.get(blocker, "Charging {window}.")
        return _clip(f"{prefix}{template.format(window=clause)}")

    if status is PlanStatus.NEEDS_CHARGE:
        return _clip(
            prefix
            + _needs_charge_clause(
                plan=plan,
                requirement=requirement,
                now=now,
                ready_by=ready_by,
                currency=currency,
            )
        )

    if status is PlanStatus.IDLE:
        if current_soc_pct is not None and target_soc_pct is not None:
            return (
                f"{prefix}No charging needed; battery is at {current_soc_pct:.0f}% "
                f"of a {target_soc_pct:.0f}% target."
            )
        return f"{prefix}No charging needed."

    return status.value.replace("_", " ").capitalize()
