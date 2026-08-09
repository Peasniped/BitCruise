"""Pure charging planner.

No Home Assistant, no I/O, no clock reads: everything comes from ``PlanningInput``
and the same input always produces the same plan. See ADR-002.

V1 plans a single contiguous window. Splitting a charge across non-contiguous
cheap intervals is deliberately deferred until contiguous planning is proven.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from itertools import pairwise

from .models import (
    ChargePlan,
    ChargeRequirement,
    ChargeUrgency,
    InvalidPlanningInput,
    PlanningInput,
    PlanPriceQuality,
    PriceInterval,
    PriceQuality,
    to_utc,
)

# Energy comparisons are done with a small tolerance so that a window which
# delivers exactly the required energy is not rejected by floating point noise.
_ENERGY_EPSILON_KWH = 1e-9


def compute_requirement(data: PlanningInput) -> ChargeRequirement:
    """Work out how much energy is needed, ignoring scheduling entirely.

    The target deficit and the reserve floor deficit are computed independently.
    Because the floor may never exceed the target, a floor deficit always implies
    a target deficit: the floor changes *when* charging must happen, never *how
    much* energy the target ultimately requires. See DESIGN.md 6.4.
    """
    deficit_pct = max(data.target_soc_pct - data.current_soc_pct, 0.0)
    battery_deficit_kwh = data.usable_capacity_kwh * deficit_pct / 100.0
    grid_required_kwh = battery_deficit_kwh / data.charging_efficiency

    floor_deficit_pct = max(data.reserve_floor_pct - data.current_soc_pct, 0.0)
    floor_deficit_kwh = data.usable_capacity_kwh * floor_deficit_pct / 100.0

    return ChargeRequirement(
        deficit_pct=deficit_pct,
        battery_deficit_kwh=battery_deficit_kwh,
        grid_energy_required_kwh=grid_required_kwh,
        required_hours=grid_required_kwh / data.charging_power_kw,
        floor_deficit_pct=floor_deficit_pct,
        floor_deficit_kwh=floor_deficit_kwh,
        urgency=(
            ChargeUrgency.URGENT if floor_deficit_pct > 0.0 else ChargeUrgency.NORMAL
        ),
    )


def plan_charging(data: PlanningInput) -> ChargePlan:
    """Produce the cheapest contiguous charging window that meets the target.

    If the target cannot be met before the deadline, returns the best effort that
    delivers the most energy, with ``can_meet_target`` false and ``shortfall_kwh``
    set. The planner never pretends a target is reachable when it is not.
    """
    requirement = compute_requirement(data)

    if not requirement.is_charge_needed:
        return _no_charge_plan(data, requirement)

    usable = _prepare_intervals(
        data.price_intervals, data.earliest_start, data.ready_by
    )
    candidate = _select_window(usable, data, requirement.grid_energy_required_kwh)

    if candidate is None:
        return _no_window_plan(data, requirement)

    return _build_plan(data, requirement, candidate)


def _prepare_intervals(
    intervals: tuple[PriceInterval, ...],
    window_start: datetime,
    window_end: datetime,
) -> tuple[PriceInterval, ...]:
    """Sort, validate and clip price intervals to the planning window.

    Input order is never trusted. Overlapping intervals are rejected rather than
    silently resolved, because the correct resolution depends on the price source
    and guessing produces a plausible but wrong cost.
    """
    ordered = sorted(intervals, key=lambda iv: to_utc(iv.start))

    for previous, current in pairwise(ordered):
        if to_utc(current.start) < to_utc(previous.end):
            raise InvalidPlanningInput(
                "price intervals overlap: "
                f"{previous.start}->{previous.end} and "
                f"{current.start}->{current.end}"
            )

    clipped = [iv.clipped_to(window_start, window_end) for iv in ordered]
    return tuple(iv for iv in clipped if iv is not None)


def _contiguous_runs(
    intervals: tuple[PriceInterval, ...],
) -> list[tuple[PriceInterval, ...]]:
    """Split intervals into runs with no gaps between them.

    A gap breaks contiguity. Charging cannot span a period with no known price,
    because the cost of that period is unknown rather than zero.
    """
    if not intervals:
        return []

    runs: list[list[PriceInterval]] = [[intervals[0]]]
    for previous, current in pairwise(intervals):
        if to_utc(current.start) == to_utc(previous.end):
            runs[-1].append(current)
        else:
            runs.append([current])
    return [tuple(run) for run in runs]


def _cost_of(
    intervals: tuple[PriceInterval, ...], power_kw: float, energy_limit_kwh: float
) -> Decimal:
    """Cost of drawing up to ``energy_limit_kwh`` across intervals, in order.

    Money is summed as Decimal so repeated addition stays exact. Energy stays as
    float and is converted at the multiplication boundary.
    """
    remaining = energy_limit_kwh
    total = Decimal("0")
    for interval in intervals:
        if remaining <= 0:
            break
        drawn = min(interval.energy_kwh(power_kw), remaining)
        total += interval.price_per_kwh * Decimal(str(drawn))
        remaining -= drawn
    return total


class _Candidate:
    """A contiguous run of intervals considered as a charging window."""

    __slots__ = ("allocated_kwh", "cost", "intervals", "meets_target", "planned_kwh")

    def __init__(
        self,
        intervals: tuple[PriceInterval, ...],
        power_kw: float,
        required_kwh: float,
    ) -> None:
        """Evaluate a window."""
        self.intervals = intervals
        self.allocated_kwh = sum(iv.energy_kwh(power_kw) for iv in intervals)
        self.meets_target = self.allocated_kwh + _ENERGY_EPSILON_KWH >= required_kwh
        self.planned_kwh = min(self.allocated_kwh, required_kwh)
        self.cost = _cost_of(intervals, power_kw, self.planned_kwh)


def _select_window(
    intervals: tuple[PriceInterval, ...],
    data: PlanningInput,
    required_kwh: float,
) -> _Candidate | None:
    """Choose the cheapest contiguous window, or the best effort if none suffices.

    Ties are broken by earliest start so that repeated planning over identical
    inputs is deterministic, which matters because a "changed" plan triggers a
    fresh approval request.
    """
    runs = _contiguous_runs(intervals)
    if not runs:
        return None

    feasible: list[_Candidate] = []
    for run in runs:
        for first in range(len(run)):
            accumulated = 0.0
            for last in range(first, len(run)):
                accumulated += run[last].energy_kwh(data.charging_power_kw)
                if accumulated + _ENERGY_EPSILON_KWH >= required_kwh:
                    feasible.append(
                        _Candidate(
                            run[first : last + 1], data.charging_power_kw, required_kwh
                        )
                    )
                    break

    if feasible:
        return min(feasible, key=lambda c: (c.cost, to_utc(c.intervals[0].start)))

    # Nothing can meet the target. Deliver as much as possible, preferring the
    # cheapest run when several deliver the same energy.
    best_effort = [
        _Candidate(run, data.charging_power_kw, required_kwh) for run in runs
    ]
    return max(
        best_effort,
        key=lambda c: (
            c.allocated_kwh,
            -c.cost,
            -c.intervals[0].start.timestamp(),
        ),
    )


def _plan_price_quality(
    intervals: tuple[PriceInterval, ...],
) -> PlanPriceQuality | None:
    """Aggregate interval qualities into a single plan-level quality."""
    if not intervals:
        return None
    qualities = {interval.quality for interval in intervals}
    if qualities == {PriceQuality.ACTUAL}:
        return PlanPriceQuality.ACTUAL
    if qualities == {PriceQuality.FORECAST}:
        return PlanPriceQuality.FORECAST
    return PlanPriceQuality.MIXED


def _plan_id(*parts: object) -> str:
    """Derive a stable identifier from plan content.

    Identical inputs produce an identical id, so the orchestration layer can tell
    a genuinely new plan from a recalculation that changed nothing. The moment
    of calculation is deliberately excluded: including it would make every
    recomputation look like a new plan, and the approval machine would re-ask
    for a window the user had already answered on.
    """
    payload = "|".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _estimated_soc(data: PlanningInput, planned_grid_kwh: float) -> float:
    """State of charge expected once the planned energy has been delivered."""
    delivered_battery_kwh = planned_grid_kwh * data.charging_efficiency
    gain_pct = delivered_battery_kwh / data.usable_capacity_kwh * 100.0
    return min(data.current_soc_pct + gain_pct, 100.0)


def _no_charge_plan(data: PlanningInput, requirement: ChargeRequirement) -> ChargePlan:
    """Plan for a car that is already at or above its target."""
    return ChargePlan(
        id=_plan_id("no-charge", data.current_soc_pct, data.target_soc_pct),
        created_at=data.now,
        start=None,
        end=None,
        current_soc_pct=data.current_soc_pct,
        target_soc_pct=data.target_soc_pct,
        reserve_floor_pct=data.reserve_floor_pct,
        required_battery_kwh=0.0,
        required_grid_kwh=0.0,
        planned_grid_kwh=0.0,
        allocated_grid_kwh=0.0,
        estimated_soc_at_end=data.current_soc_pct,
        estimated_cost=None,
        can_meet_target=True,
        shortfall_kwh=0.0,
        price_quality=None,
        urgency=requirement.urgency,
        below_reserve_floor=requirement.below_reserve_floor,
        intervals=(),
    )


def _no_window_plan(data: PlanningInput, requirement: ChargeRequirement) -> ChargePlan:
    """Plan for when charging is needed but no usable price interval exists."""
    return ChargePlan(
        id=_plan_id("no-window", data.current_soc_pct, data.target_soc_pct),
        created_at=data.now,
        start=None,
        end=None,
        current_soc_pct=data.current_soc_pct,
        target_soc_pct=data.target_soc_pct,
        reserve_floor_pct=data.reserve_floor_pct,
        required_battery_kwh=requirement.battery_deficit_kwh,
        required_grid_kwh=requirement.grid_energy_required_kwh,
        planned_grid_kwh=0.0,
        allocated_grid_kwh=0.0,
        estimated_soc_at_end=data.current_soc_pct,
        estimated_cost=None,
        can_meet_target=False,
        shortfall_kwh=requirement.grid_energy_required_kwh,
        price_quality=None,
        urgency=requirement.urgency,
        below_reserve_floor=requirement.below_reserve_floor,
        intervals=(),
    )


def _build_plan(
    data: PlanningInput, requirement: ChargeRequirement, candidate: _Candidate
) -> ChargePlan:
    """Assemble the final plan from a chosen window."""
    start = candidate.intervals[0].start
    end = candidate.intervals[-1].end
    shortfall = max(requirement.grid_energy_required_kwh - candidate.planned_kwh, 0.0)

    return ChargePlan(
        id=_plan_id(start, end, requirement.grid_energy_required_kwh),
        created_at=data.now,
        start=start,
        end=end,
        current_soc_pct=data.current_soc_pct,
        target_soc_pct=data.target_soc_pct,
        reserve_floor_pct=data.reserve_floor_pct,
        required_battery_kwh=requirement.battery_deficit_kwh,
        required_grid_kwh=requirement.grid_energy_required_kwh,
        planned_grid_kwh=candidate.planned_kwh,
        allocated_grid_kwh=candidate.allocated_kwh,
        estimated_soc_at_end=_estimated_soc(data, candidate.planned_kwh),
        estimated_cost=candidate.cost,
        can_meet_target=candidate.meets_target,
        shortfall_kwh=shortfall,
        price_quality=_plan_price_quality(candidate.intervals),
        urgency=requirement.urgency,
        below_reserve_floor=requirement.below_reserve_floor,
        intervals=candidate.intervals,
    )
