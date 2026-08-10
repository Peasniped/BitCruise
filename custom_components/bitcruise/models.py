"""Domain models for BitCruise charge planning.

This module is pure Python. It must never import Home Assistant, and nothing here
may perform I/O. See ADR-002.

All datetimes are timezone-aware. Naive datetimes are rejected at construction
rather than silently interpreted, because a naive value that survives into the
planner produces a plan that is wrong by whatever the UTC offset happens to be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class PlanningError(ValueError):
    """Base class for planning errors caused by invalid input."""


class InvalidPlanningInput(PlanningError):
    """The inputs cannot produce a meaningful plan and must not be guessed at."""


class PriceQuality(StrEnum):
    """Whether a single price interval is settled or predicted."""

    ACTUAL = "actual"
    FORECAST = "forecast"


class PlanPriceQuality(StrEnum):
    """The aggregate price quality of a plan."""

    ACTUAL = "actual"
    FORECAST = "forecast"
    MIXED = "mixed"


class ChargeUrgency(StrEnum):
    """How freely the planner may optimize for cost.

    NORMAL is deadline-driven: optimize cost anywhere before the deadline.
    URGENT means the battery is below the reserve floor, so the car may not be
    drivable right now. See DESIGN.md 6.4 and ADR-007.
    """

    NORMAL = "normal"
    URGENT = "urgent"


class PlanStatus(StrEnum):
    """Lifecycle state of a plan.

    Owned by the orchestration layer, not the planner. The planner is pure and
    returns a ChargePlan; it never decides whether that plan is approved.
    """

    IDLE = "idle"
    NEEDS_CHARGE = "needs_charge"
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    WAITING_FOR_CAR = "waiting_for_car"
    READY = "ready"
    CHARGING = "charging"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class PlanSource(StrEnum):
    """Why a plan was produced."""

    INITIAL = "initial"
    PRICE_UPDATE = "price_update"
    SOC_CHANGE = "soc_change"
    SETTINGS_CHANGE = "settings_change"
    MANUAL = "manual"
    SCHEDULE = "schedule"
    """The clock moved past a deadline or a price interval; nothing else changed."""


class ApprovalPolicy(StrEnum):
    """How much the user wants to be asked before charging happens.

    ADR-003 says an approved plan is never *silently* changed. AUTOMATIC is the
    one way out of that, and it is not silent: the user has to choose it, and
    the plan status attributes still report every move. It exists because being
    asked is only worth it if the answer is ever going to be "no".
    """

    ALWAYS_ASK = "always_ask"
    ASK_ON_CHANGE = "ask_on_change"
    AUTOMATIC = "automatic"


def _require_aware(value: datetime, name: str) -> None:
    """Reject naive datetimes."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise InvalidPlanningInput(f"{name} must be timezone-aware, got {value!r}")


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC.

    Every comparison and duration in this package goes through here, because
    Python compares and subtracts two datetimes sharing a ``tzinfo`` using
    wall-clock arithmetic. Across a DST transition that is wrong twice a year:
    01:00 to 03:00 local on a spring-forward day subtracts to two hours when only
    one hour actually elapses, and the repeated hour on a fall-back day makes two
    genuinely different instants compare equal.
    """
    return value.astimezone(UTC)


def elapsed_hours(start: datetime, end: datetime) -> float:
    """Hours that actually elapse between two instants, DST-correct."""
    return (to_utc(end) - to_utc(start)).total_seconds() / 3600.0


def _later(first: datetime, second: datetime) -> datetime:
    """Return whichever instant is later in absolute time."""
    return first if to_utc(first) >= to_utc(second) else second


def _earlier(first: datetime, second: datetime) -> datetime:
    """Return whichever instant is earlier in absolute time."""
    return first if to_utc(first) <= to_utc(second) else second


@dataclass(frozen=True, slots=True)
class PriceInterval:
    """A single priced period of time.

    Duration is derived from the absolute difference between the two aware
    datetimes, so a period spanning a DST transition reports the number of hours
    that actually elapse rather than the wall-clock difference.
    """

    start: datetime
    end: datetime
    price_per_kwh: Decimal
    quality: PriceQuality = PriceQuality.ACTUAL

    def __post_init__(self) -> None:
        """Validate the interval."""
        _require_aware(self.start, "PriceInterval.start")
        _require_aware(self.end, "PriceInterval.end")
        if to_utc(self.end) <= to_utc(self.start):
            raise InvalidPlanningInput(
                f"PriceInterval.end must be after start: {self.start} -> {self.end}"
            )

    @property
    def duration_hours(self) -> float:
        """Elapsed hours, DST-correct."""
        return elapsed_hours(self.start, self.end)

    def energy_kwh(self, power_kw: float) -> float:
        """Energy deliverable across this interval at a constant power."""
        return self.duration_hours * power_kw

    def clipped_to(self, start: datetime, end: datetime) -> PriceInterval | None:
        """Return this interval truncated to a window, or None if it falls outside."""
        new_start = _later(self.start, start)
        new_end = _earlier(self.end, end)
        if to_utc(new_end) <= to_utc(new_start):
            return None
        if new_start == self.start and new_end == self.end:
            return self
        return PriceInterval(
            start=new_start,
            end=new_end,
            price_per_kwh=self.price_per_kwh,
            quality=self.quality,
        )


@dataclass(frozen=True, slots=True)
class PlanningInput:
    """Everything the planner needs to produce a plan.

    ``reserve_floor_pct`` is the state of charge below which the car is considered
    not reasonably drivable. It is independent of ``target_soc_pct``: the target
    answers "ready by the deadline?", the floor answers "can we drive right now?".
    A floor of 0 disables the concept entirely. See DESIGN.md 6.4.
    """

    now: datetime
    current_soc_pct: float
    target_soc_pct: float
    usable_capacity_kwh: float
    charging_power_kw: float
    ready_by: datetime
    price_intervals: tuple[PriceInterval, ...] = ()
    charging_efficiency: float = 0.9
    reserve_floor_pct: float = 0.0
    not_before: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the inputs, refusing to guess at anything ambiguous."""
        _require_aware(self.now, "PlanningInput.now")
        _require_aware(self.ready_by, "PlanningInput.ready_by")
        if self.not_before is not None:
            _require_aware(self.not_before, "PlanningInput.not_before")

        for name, value in (
            ("current_soc_pct", self.current_soc_pct),
            ("target_soc_pct", self.target_soc_pct),
            ("reserve_floor_pct", self.reserve_floor_pct),
        ):
            if not 0.0 <= value <= 100.0:
                raise InvalidPlanningInput(f"{name} must be 0..100, got {value}")

        if self.usable_capacity_kwh <= 0:
            raise InvalidPlanningInput(
                f"usable_capacity_kwh must be > 0, got {self.usable_capacity_kwh}"
            )
        if self.charging_power_kw <= 0:
            raise InvalidPlanningInput(
                f"charging_power_kw must be > 0, got {self.charging_power_kw}"
            )
        if not 0.0 < self.charging_efficiency <= 1.0:
            raise InvalidPlanningInput(
                f"charging_efficiency must be in (0, 1], got {self.charging_efficiency}"
            )
        if self.reserve_floor_pct > self.target_soc_pct:
            raise InvalidPlanningInput(
                f"reserve_floor_pct ({self.reserve_floor_pct}) must not exceed "
                f"target_soc_pct ({self.target_soc_pct}). The floor is a lower "
                "bound on availability, not a second target."
            )

    @property
    def earliest_start(self) -> datetime:
        """The earliest moment charging may begin."""
        if self.not_before is None:
            return self.now
        return _later(self.now, self.not_before)


@dataclass(frozen=True, slots=True)
class ChargeRequirement:
    """How much energy is needed, before any scheduling is considered."""

    deficit_pct: float
    battery_deficit_kwh: float
    grid_energy_required_kwh: float
    required_hours: float
    floor_deficit_pct: float
    floor_deficit_kwh: float
    urgency: ChargeUrgency

    @property
    def is_charge_needed(self) -> bool:
        """Whether any charging is required at all."""
        return self.deficit_pct > 0.0

    @property
    def below_reserve_floor(self) -> bool:
        """Whether the car is currently below the reserve floor."""
        return self.floor_deficit_pct > 0.0


@dataclass(frozen=True, slots=True)
class ChargePlan:
    """A proposed charging window. Produced without side effects.

    ``planned_grid_kwh`` is the energy expected to be drawn. ``allocated_grid_kwh``
    is what the booked window could deliver if run end to end. They differ because
    V1 allocates whole price intervals, so the final interval is usually longer
    than strictly needed; ``over_allocation_kwh`` reports that slack.
    """

    id: str
    created_at: datetime
    start: datetime | None
    end: datetime | None
    current_soc_pct: float
    target_soc_pct: float
    reserve_floor_pct: float
    required_battery_kwh: float
    required_grid_kwh: float
    planned_grid_kwh: float
    allocated_grid_kwh: float
    estimated_soc_at_end: float
    estimated_cost: Decimal | None
    can_meet_target: bool
    shortfall_kwh: float
    price_quality: PlanPriceQuality | None
    urgency: ChargeUrgency
    below_reserve_floor: bool
    intervals: tuple[PriceInterval, ...] = ()

    @property
    def is_charge_needed(self) -> bool:
        """Whether this plan involves charging at all."""
        return self.required_grid_kwh > 0.0

    @property
    def has_window(self) -> bool:
        """Whether a concrete charging window was found."""
        return self.start is not None and self.end is not None

    @property
    def over_allocation_kwh(self) -> float:
        """Energy the window could deliver beyond what is expected to be drawn."""
        return max(self.allocated_grid_kwh - self.planned_grid_kwh, 0.0)

    @property
    def duration_hours(self) -> float:
        """Length of the booked window in hours, DST-correct."""
        if self.start is None or self.end is None:
            return 0.0
        return elapsed_hours(self.start, self.end)
