"""The proposal/approval state machine.

Pure Python: no Home Assistant, no entities, no storage. The coordinator hands
in the record it last persisted plus a freshly optimized candidate, and gets
back the record that should now hold. Every rule in DESIGN.md section 7 lives
here and nowhere else, so approval logic is never duplicated in a button, a
notification handler, or a service call.

The load-bearing guarantee is ADR-003: an approved plan is never changed without
the user's say-so. A replan that moves the window materially is *staged* as a
proposal and the approved plan keeps running until the user answers. The one
exception is ``ApprovalPolicy.AUTOMATIC``, where the say-so was given once by
choosing the policy rather than nightly by pressing a button.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    ApprovalPolicy,
    ChargePlan,
    ChargeUrgency,
    PlanPriceQuality,
    PlanSource,
    PriceInterval,
    to_utc,
)


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """What the approval machine remembers between evaluations.

    ``rejected_plan_id`` exists because plan ids are derived from content: a
    recalculation moments after a rejection produces the identical plan, and
    without this it would be proposed again immediately, forever.
    """

    approved: ChargePlan | None = None
    proposal: ChargePlan | None = None
    proposal_reason: PlanSource | None = None
    rejected_plan_id: str | None = None
    completed_plan_id: str | None = None

    @property
    def requires_approval(self) -> bool:
        """Whether a proposal is waiting for the user to answer."""
        return self.proposal is not None

    @property
    def is_replacement(self) -> bool:
        """Whether the pending proposal would displace an approved plan."""
        return self.proposal is not None and self.approved is not None


def windows_equivalent(
    left: ChargePlan | None,
    right: ChargePlan | None,
    tolerance: timedelta,
    *,
    started: bool = False,
) -> bool:
    """Whether two plans book close enough to the same window to not ask again.

    The comparison is strict at the boundary: with the default tolerance of one
    price interval, a window that moves by a whole interval is a material
    change. Anything smaller is jitter within a slot and is not worth a prompt.

    ``started`` drops the start time from the comparison, and is set once the
    clock is inside the approved window. A replan can never propose starting in
    the past, so its start creeps forward with the clock and would eventually
    differ by any tolerance you pick — asking the user, every hour, to approve
    the fact that time has passed. Once a window is running, only its *end*
    moving is a real change.
    """
    if left is None or right is None:
        return left is right
    if not left.has_window or not right.has_window:
        return left.has_window == right.has_window
    if abs(to_utc(left.end) - to_utc(right.end)) >= tolerance:
        return False
    if started:
        return True
    return abs(to_utc(left.start) - to_utc(right.start)) < tolerance


def _has_started(plan: ChargePlan | None, now: datetime) -> bool:
    """Whether the clock has reached a plan's window."""
    if plan is None or plan.start is None:
        return False
    return to_utc(now) >= to_utc(plan.start)


def _expire(record: PlanRecord, now: datetime) -> PlanRecord:
    """Retire an approved plan whose window has already ended.

    Without this the record would still be holding last night's approval, and
    tonight's plan would be treated as a replacement for a window in the past.
    """
    approved = record.approved
    if approved is None or approved.end is None:
        return record
    if to_utc(approved.end) > to_utc(now):
        return record
    return PlanRecord(completed_plan_id=approved.id)


def reconcile(
    record: PlanRecord,
    candidate: ChargePlan | None,
    *,
    now: datetime,
    policy: ApprovalPolicy,
    material_change: timedelta,
    reason: PlanSource,
    smart_charging: bool = True,
    inputs_usable: bool = True,
) -> PlanRecord:
    """Fold a freshly optimized candidate into the stored record.

    ``inputs_usable`` is False when the evaluation could not be trusted — a
    source entity unavailable, a bad configuration. The record is then carried
    forward untouched, because a price sensor blinking out for one update must
    never discard a proposal the user was about to answer.
    """
    if not smart_charging:
        # "Smart charging off" means BitCruise is not deciding anything. The
        # deficit sensors keep reporting; nothing is proposed or held approved.
        return PlanRecord()

    record = _expire(record, now)

    if not inputs_usable:
        return record

    if candidate is None or not candidate.has_window:
        # Nothing to schedule. Any pending proposal is stale, but an approved
        # plan stands: withdrawing it here would be exactly the silent change
        # ADR-003 forbids.
        return replace(record, proposal=None, proposal_reason=None)

    automatic = policy is ApprovalPolicy.AUTOMATIC

    # A rejection is an answer to a question. Under AUTOMATIC no question is
    # ever asked, so honouring one left over from another policy would suppress
    # a window forever with nothing on screen to explain it. A *completed* plan
    # is different: re-approving last night's finished window is wrong under
    # every policy.
    stale = (
        (record.completed_plan_id,)
        if automatic
        else (
            record.rejected_plan_id,
            record.completed_plan_id,
        )
    )
    if candidate.id in stale:
        return replace(record, proposal=None, proposal_reason=None)

    if record.approved is None:
        if automatic or policy is ApprovalPolicy.ASK_ON_CHANGE:
            return PlanRecord(
                approved=candidate, completed_plan_id=record.completed_plan_id
            )
        return replace(record, proposal=candidate, proposal_reason=reason)

    if windows_equivalent(
        record.approved,
        candidate,
        material_change,
        started=_has_started(record.approved, now),
    ):
        # The replan agrees with what is already approved. Drop any staged
        # replacement: whatever moved the window has moved back.
        return replace(record, proposal=None, proposal_reason=None)

    if automatic:
        return PlanRecord(
            approved=candidate, completed_plan_id=record.completed_plan_id
        )

    return replace(record, proposal=candidate, proposal_reason=reason)


def accept(record: PlanRecord) -> PlanRecord:
    """Promote the pending proposal to approved, atomically.

    Replacing an approved plan and approving a first plan are the same
    operation, which is why there is no separate "accept move" path.
    """
    if record.proposal is None:
        return record
    return PlanRecord(
        approved=record.proposal, completed_plan_id=record.completed_plan_id
    )


def reject(record: PlanRecord) -> PlanRecord:
    """Discard the pending proposal, leaving any approved plan untouched."""
    if record.proposal is None:
        return record
    return replace(
        record,
        proposal=None,
        proposal_reason=None,
        rejected_plan_id=record.proposal.id,
    )


def clear_rejection(record: PlanRecord) -> PlanRecord:
    """Forget the last rejection so the same window can be proposed again.

    Pressing recalculate is an explicit request to reconsider, including a plan
    that was previously turned down.
    """
    return replace(record, rejected_plan_id=None)


def price_fingerprint(intervals: Sequence[PriceInterval]) -> str:
    """Digest a price curve, so a refresh that changed nothing is recognisable.

    Energi Data Service republishes the same attributes routinely. Comparing the
    digest rather than the object identity keeps "the prices changed" an honest
    statement about content.
    """
    payload = "|".join(
        f"{to_utc(interval.start).isoformat()}={interval.price_per_kwh}"
        f":{interval.quality.value}"
        for interval in intervals
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """Everything that can move a window, captured so a replan can be explained."""

    current_soc_pct: float | None = None
    target_soc_pct: float | None = None
    usable_capacity_kwh: float | None = None
    charging_power_kw: float | None = None
    ready_by: datetime | None = None
    prices: str = ""


def proposal_reason(
    previous: PlanInputs | None, current: PlanInputs, *, manual: bool = False
) -> PlanSource:
    """Name the input that most likely caused this replan.

    Checked in order of how directly each one explains a moved window. This is a
    label for the user, not a control signal: nothing downstream branches on it.
    """
    if manual:
        return PlanSource.MANUAL
    if previous is None:
        return PlanSource.INITIAL
    if previous.current_soc_pct != current.current_soc_pct:
        return PlanSource.SOC_CHANGE
    if previous.prices != current.prices:
        return PlanSource.PRICE_UPDATE
    if (
        previous.target_soc_pct != current.target_soc_pct
        or previous.usable_capacity_kwh != current.usable_capacity_kwh
        or previous.charging_power_kw != current.charging_power_kw
        or previous.ready_by != current.ready_by
    ):
        return PlanSource.SETTINGS_CHANGE
    return PlanSource.SCHEDULE


# --- Persistence -------------------------------------------------------------
#
# Serialization lives here rather than in storage.py so it stays importable
# without Home Assistant, and so the round trip can be tested on Windows.
#
# Price intervals are deliberately not persisted. They are large, they are
# re-derived from the price entity on every evaluation, and after a restart the
# only thing that matters about an approved plan is the window it booked.

_PLAN_NUMBERS: tuple[str, ...] = (
    "current_soc_pct",
    "target_soc_pct",
    "reserve_floor_pct",
    "required_battery_kwh",
    "required_grid_kwh",
    "planned_grid_kwh",
    "allocated_grid_kwh",
    "estimated_soc_at_end",
    "shortfall_kwh",
)


def _plan_to_dict(plan: ChargePlan) -> dict[str, object]:
    """Flatten a plan into JSON-safe primitives."""
    data: dict[str, object] = {
        "id": plan.id,
        "created_at": plan.created_at.isoformat(),
        "start": plan.start.isoformat() if plan.start else None,
        "end": plan.end.isoformat() if plan.end else None,
        "estimated_cost": (
            str(plan.estimated_cost) if plan.estimated_cost is not None else None
        ),
        "can_meet_target": plan.can_meet_target,
        "price_quality": plan.price_quality.value if plan.price_quality else None,
        "urgency": plan.urgency.value,
        "below_reserve_floor": plan.below_reserve_floor,
    }
    data.update({field: getattr(plan, field) for field in _PLAN_NUMBERS})
    return data


def _plan_from_dict(data: object) -> ChargePlan | None:
    """Rebuild a plan, returning None for anything that does not parse.

    A corrupt or older payload must not stop the integration from loading; the
    worst case is that the approval is forgotten and a fresh plan is proposed.
    """
    if not isinstance(data, dict):
        return None
    try:
        return ChargePlan(
            id=str(data["id"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            start=(
                datetime.fromisoformat(str(data["start"]))
                if data.get("start")
                else None
            ),
            end=datetime.fromisoformat(str(data["end"])) if data.get("end") else None,
            estimated_cost=(
                Decimal(str(data["estimated_cost"]))
                if data.get("estimated_cost") is not None
                else None
            ),
            can_meet_target=bool(data["can_meet_target"]),
            price_quality=(
                PlanPriceQuality(data["price_quality"])
                if data.get("price_quality")
                else None
            ),
            urgency=ChargeUrgency(data["urgency"]),
            below_reserve_floor=bool(data["below_reserve_floor"]),
            **{field: float(data[field]) for field in _PLAN_NUMBERS},
        )
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


@dataclass(frozen=True, slots=True)
class StoredState:
    """Everything that must outlive a restart.

    ``approval_policy`` is None when the store predates it being a control the
    user can change at runtime. The coordinator then falls back to the config
    entry option it used to live in, so an existing installation keeps the
    policy it was set up with.
    """

    record: PlanRecord = field(default_factory=PlanRecord)
    smart_charging: bool = True
    approval_policy: ApprovalPolicy | None = None


def stored_state_to_dict(state: StoredState) -> dict[str, Any]:
    """Serialize everything that must outlive a restart."""
    record = state.record
    return {
        "smart_charging": state.smart_charging,
        "approval_policy": (
            state.approval_policy.value if state.approval_policy else None
        ),
        "approved": _plan_to_dict(record.approved) if record.approved else None,
        "proposal": _plan_to_dict(record.proposal) if record.proposal else None,
        "proposal_reason": (
            record.proposal_reason.value if record.proposal_reason else None
        ),
        "rejected_plan_id": record.rejected_plan_id,
        "completed_plan_id": record.completed_plan_id,
    }


def stored_state_from_dict(data: object) -> StoredState:
    """Restore the persisted state, tolerating junk."""
    if not isinstance(data, dict):
        return StoredState()

    reason: PlanSource | None = None
    raw_reason = data.get("proposal_reason")
    if raw_reason is not None:
        try:
            reason = PlanSource(raw_reason)
        except ValueError:
            reason = None

    policy: ApprovalPolicy | None = None
    raw_policy = data.get("approval_policy")
    if raw_policy is not None:
        try:
            policy = ApprovalPolicy(raw_policy)
        except ValueError:
            policy = None

    proposal = _plan_from_dict(data.get("proposal"))
    record = PlanRecord(
        approved=_plan_from_dict(data.get("approved")),
        proposal=proposal,
        proposal_reason=reason if proposal is not None else None,
        rejected_plan_id=_optional_str(data.get("rejected_plan_id")),
        completed_plan_id=_optional_str(data.get("completed_plan_id")),
    )
    return StoredState(
        record=record,
        smart_charging=bool(data.get("smart_charging", True)),
        approval_policy=policy,
    )


def _optional_str(value: object) -> str | None:
    """Keep a string, discard anything else."""
    return value if isinstance(value, str) else None
