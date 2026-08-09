"""Tests for the pure domain models."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from custom_components.bitcruise.models import (
    ChargeUrgency,
    InvalidPlanningInput,
    PriceInterval,
    PriceQuality,
    elapsed_hours,
)

from .builders import CPH, at, planning_input


def interval(
    start_hour: int,
    price: str,
    hours: float = 1.0,
    quality: PriceQuality = PriceQuality.ACTUAL,
) -> PriceInterval:
    """Build a single price interval starting at a whole hour."""
    start = at(start_hour)
    return PriceInterval(
        start=start,
        end=start + timedelta(hours=hours),
        price_per_kwh=Decimal(price),
        quality=quality,
    )


class TestPriceInterval:
    """Price interval construction and derived values."""

    def test_duration_hours(self) -> None:
        assert interval(1, "1.0", hours=1.0).duration_hours == 1.0
        assert interval(1, "1.0", hours=0.25).duration_hours == 0.25

    def test_energy_at_power(self) -> None:
        assert interval(1, "1.0", hours=1.0).energy_kwh(10.0) == 10.0
        assert interval(1, "1.0", hours=0.25).energy_kwh(10.0) == 2.5

    def test_naive_datetimes_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="timezone-aware"):
            PriceInterval(
                start=datetime(2026, 8, 10, 1),  # noqa: DTZ001 - deliberately naive
                end=datetime(2026, 8, 10, 2),  # noqa: DTZ001 - deliberately naive
                price_per_kwh=Decimal("1.0"),
            )

    def test_end_before_start_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="must be after start"):
            PriceInterval(start=at(3), end=at(2), price_per_kwh=Decimal("1.0"))

    def test_zero_length_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="must be after start"):
            PriceInterval(start=at(3), end=at(3), price_per_kwh=Decimal("1.0"))

    def test_clipping_truncates(self) -> None:
        clipped = interval(1, "1.0", hours=2.0).clipped_to(at(2), at(4))
        assert clipped is not None
        assert clipped.start == at(2)
        assert clipped.end == at(3)

    def test_clipping_outside_window_returns_none(self) -> None:
        assert interval(1, "1.0").clipped_to(at(5), at(6)) is None

    def test_clipping_preserves_price_and_quality(self) -> None:
        original = interval(1, "2.5", hours=2.0, quality=PriceQuality.FORECAST)
        clipped = original.clipped_to(at(1, 30), at(4))
        assert clipped is not None
        assert clipped.price_per_kwh == Decimal("2.5")
        assert clipped.quality is PriceQuality.FORECAST


class TestDaylightSavingArithmetic:
    """Regression tests for the two ways Python gets DST wrong.

    Python performs wall-clock arithmetic when two aware datetimes share a
    ``tzinfo``. Both of these assertions failed before ``to_utc`` was introduced.
    """

    def test_duration_is_elapsed_time_not_wall_clock(self) -> None:
        """01:00 to 03:00 on a spring-forward day is one hour, not two."""
        start = datetime(2026, 3, 29, 1, tzinfo=CPH)
        end = datetime(2026, 3, 29, 3, tzinfo=CPH)
        assert PriceInterval(start, end, Decimal("1.0")).duration_hours == 1.0
        # The trap this avoids: naive subtraction claims two hours.
        assert (end - start).total_seconds() / 3600.0 == 2.0

    def test_elapsed_hours_across_fall_back(self) -> None:
        """02:00 to 03:00 on a fall-back day is two hours, not one."""
        start = datetime(2026, 10, 25, 2, tzinfo=CPH, fold=0)
        end = datetime(2026, 10, 25, 3, tzinfo=CPH)
        assert elapsed_hours(start, end) == 2.0
        assert (end - start).total_seconds() / 3600.0 == 1.0

    def test_energy_uses_elapsed_time(self) -> None:
        """A skipped hour must not be billed as delivered energy."""
        start = datetime(2026, 3, 29, 1, tzinfo=CPH)
        end = datetime(2026, 3, 29, 3, tzinfo=CPH)
        assert PriceInterval(start, end, Decimal("1.0")).energy_kwh(10.0) == 10.0


class TestPlanningInputValidation:
    """Invalid inputs must be rejected rather than guessed at."""

    @pytest.mark.parametrize("soc", [-1.0, 101.0])
    def test_soc_out_of_range_rejected(self, soc: float) -> None:
        with pytest.raises(InvalidPlanningInput, match=r"must be 0\.\.100"):
            planning_input(current_soc_pct=soc)

    def test_zero_capacity_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="usable_capacity_kwh"):
            planning_input(usable_capacity_kwh=0.0)

    def test_negative_capacity_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="usable_capacity_kwh"):
            planning_input(usable_capacity_kwh=-5.0)

    def test_zero_power_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="charging_power_kw"):
            planning_input(charging_power_kw=0.0)

    @pytest.mark.parametrize("efficiency", [0.0, -0.5, 1.5])
    def test_efficiency_out_of_range_rejected(self, efficiency: float) -> None:
        with pytest.raises(InvalidPlanningInput, match="charging_efficiency"):
            planning_input(charging_efficiency=efficiency)

    def test_efficiency_of_one_is_allowed(self) -> None:
        assert planning_input(charging_efficiency=1.0).charging_efficiency == 1.0

    def test_floor_above_target_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="must not exceed"):
            planning_input(target_soc_pct=80.0, reserve_floor_pct=90.0)

    def test_floor_equal_to_target_is_allowed(self) -> None:
        data = planning_input(target_soc_pct=80.0, reserve_floor_pct=80.0)
        assert data.reserve_floor_pct == 80.0

    def test_naive_now_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="timezone-aware"):
            planning_input(now=datetime(2026, 8, 10, 20))  # noqa: DTZ001

    def test_naive_ready_by_rejected(self) -> None:
        with pytest.raises(InvalidPlanningInput, match="timezone-aware"):
            planning_input(ready_by=datetime(2026, 8, 11, 7))  # noqa: DTZ001

    def test_earliest_start_defaults_to_now(self) -> None:
        assert planning_input().earliest_start == at(0)

    def test_not_before_after_now_wins(self) -> None:
        assert planning_input(not_before=at(6)).earliest_start == at(6)

    def test_not_before_in_the_past_is_ignored(self) -> None:
        assert planning_input(not_before=at(20, day=9)).earliest_start == at(0)

    def test_reserve_floor_defaults_to_disabled(self) -> None:
        assert planning_input().reserve_floor_pct == 0.0


def test_charge_urgency_values() -> None:
    """Urgency values are stable strings, since they reach entity state."""
    assert ChargeUrgency.NORMAL == "normal"
    assert ChargeUrgency.URGENT == "urgent"
