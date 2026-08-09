"""Test data builders for the pure planner tests.

Deliberately free of Home Assistant imports so these run on any platform.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from custom_components.bitcruise.models import (
    PlanningInput,
    PriceInterval,
    PriceQuality,
)

CPH = ZoneInfo("Europe/Copenhagen")
UTC = ZoneInfo("UTC")

# Europe/Copenhagen DST transitions in 2026.
SPRING_FORWARD_DAY = datetime(2026, 3, 29, tzinfo=CPH)  # 23-hour local day
FALL_BACK_DAY = datetime(2026, 10, 25, tzinfo=CPH)  # 25-hour local day


def at(hour: int, minute: int = 0, day: int = 10, month: int = 8) -> datetime:
    """Build an aware local datetime in 2026."""
    return datetime(2026, month, day, hour, minute, tzinfo=CPH)


def advance(moment: datetime, delta: timedelta) -> datetime:
    """Advance an aware datetime by real elapsed time.

    ``moment + delta`` would add wall-clock time: on a spring-forward day that
    yields 02:00 local, an instant that does not exist. Converting through UTC
    adds actual elapsed time, which is what a price interval represents.
    """
    return (moment.astimezone(UTC) + delta).astimezone(moment.tzinfo)


def hourly(
    start: datetime,
    prices: Sequence[str],
    quality: PriceQuality = PriceQuality.ACTUAL,
) -> tuple[PriceInterval, ...]:
    """Build consecutive one-hour intervals from an aware start.

    Steps by absolute time, so a run crossing a DST boundary stays contiguous and
    reports real elapsed hours rather than wall-clock hours.
    """
    intervals = []
    cursor = start
    for price in prices:
        end = advance(cursor, timedelta(hours=1))
        intervals.append(
            PriceInterval(
                start=cursor,
                end=end,
                price_per_kwh=Decimal(price),
                quality=quality,
            )
        )
        cursor = end
    return tuple(intervals)


def quarter_hourly(
    start: datetime,
    prices: Sequence[str],
    quality: PriceQuality = PriceQuality.ACTUAL,
) -> tuple[PriceInterval, ...]:
    """Build consecutive 15-minute intervals from an aware start."""
    intervals = []
    cursor = start
    for price in prices:
        end = advance(cursor, timedelta(minutes=15))
        intervals.append(
            PriceInterval(
                start=cursor,
                end=end,
                price_per_kwh=Decimal(price),
                quality=quality,
            )
        )
        cursor = end
    return tuple(intervals)


def planning_input(**overrides: object) -> PlanningInput:
    """Build a PlanningInput with round numbers, overriding individual fields.

    Defaults give a 20 percentage point deficit on a 100 kWh battery at 100%
    efficiency and 10 kW, i.e. exactly 20 kWh over exactly two hours. Tests that
    care about awkward arithmetic override these deliberately.
    """
    defaults: dict[str, object] = {
        "now": at(0),
        "current_soc_pct": 60.0,
        "target_soc_pct": 80.0,
        "usable_capacity_kwh": 100.0,
        "charging_power_kw": 10.0,
        "ready_by": at(0, day=11),
        "charging_efficiency": 1.0,
    }
    defaults.update(overrides)
    return PlanningInput(**defaults)  # type: ignore[arg-type]
