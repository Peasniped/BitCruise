"""Tests for the pure charging planner.

Covers the matrix in DESIGN.md section 14. No Home Assistant import anywhere; if
one becomes necessary the planner has grown a dependency it should not have.
"""

from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import pytest
from custom_components.bitcruise.models import (
    ChargeUrgency,
    InvalidPlanningInput,
    PlanPriceQuality,
    PriceInterval,
    PriceQuality,
    to_utc,
)
from custom_components.bitcruise.planner import compute_requirement, plan_charging

from .builders import CPH, at, hourly, planning_input, quarter_hourly

# 24 flat-priced hours from midnight, used as a neutral backdrop.
FLAT_24 = ["2.0"] * 24


def prices_with_cheap_pair(index: int, cheap: str = "0.1") -> list[str]:
    """Flat prices with two cheap consecutive hours starting at ``index``."""
    prices = list(FLAT_24)
    prices[index] = cheap
    prices[index + 1] = cheap
    return prices


class TestDeficitCalculations:
    """Deficit maths, independent of scheduling."""

    def test_no_deficit_when_at_target(self) -> None:
        req = compute_requirement(planning_input(current_soc_pct=80.0))
        assert req.deficit_pct == 0.0
        assert req.battery_deficit_kwh == 0.0
        assert not req.is_charge_needed

    def test_no_deficit_when_above_target(self) -> None:
        """A target below current SoC is never inferred as a discharge request."""
        req = compute_requirement(planning_input(current_soc_pct=90.0))
        assert req.deficit_pct == 0.0

    @pytest.mark.parametrize(
        ("current", "target", "expected_pct", "expected_kwh"),
        [
            (70.0, 80.0, 10.0, 10.0),
            (30.0, 80.0, 50.0, 50.0),
            (0.0, 100.0, 100.0, 100.0),
            (42.0, 80.0, 38.0, 38.0),
        ],
    )
    def test_deficit_scales(
        self, current: float, target: float, expected_pct: float, expected_kwh: float
    ) -> None:
        req = compute_requirement(
            planning_input(current_soc_pct=current, target_soc_pct=target)
        )
        assert req.deficit_pct == pytest.approx(expected_pct)
        assert req.battery_deficit_kwh == pytest.approx(expected_kwh)

    def test_efficiency_increases_grid_energy(self) -> None:
        """Battery energy and grid energy stay distinct."""
        req = compute_requirement(planning_input(charging_efficiency=0.9))
        assert req.battery_deficit_kwh == pytest.approx(20.0)
        assert req.grid_energy_required_kwh == pytest.approx(20.0 / 0.9)

    def test_efficiency_of_one_leaves_energy_unchanged(self) -> None:
        req = compute_requirement(planning_input(charging_efficiency=1.0))
        assert req.grid_energy_required_kwh == pytest.approx(req.battery_deficit_kwh)

    def test_required_hours_is_not_rounded(self) -> None:
        req = compute_requirement(planning_input(charging_efficiency=0.9))
        assert req.required_hours == pytest.approx(2.2222222, abs=1e-6)


class TestReserveFloor:
    """The floor is independent of the target. See DESIGN.md 6.4, ADR-007."""

    def test_floor_of_zero_is_normal_urgency(self) -> None:
        req = compute_requirement(planning_input(reserve_floor_pct=0.0))
        assert req.urgency is ChargeUrgency.NORMAL
        assert not req.below_reserve_floor

    def test_below_floor_is_urgent(self) -> None:
        req = compute_requirement(
            planning_input(current_soc_pct=20.0, reserve_floor_pct=30.0)
        )
        assert req.urgency is ChargeUrgency.URGENT
        assert req.below_reserve_floor
        assert req.floor_deficit_pct == pytest.approx(10.0)
        assert req.floor_deficit_kwh == pytest.approx(10.0)

    def test_exactly_at_floor_is_not_urgent(self) -> None:
        req = compute_requirement(
            planning_input(current_soc_pct=30.0, reserve_floor_pct=30.0)
        )
        assert req.urgency is ChargeUrgency.NORMAL
        assert req.floor_deficit_pct == 0.0

    def test_floor_does_not_change_required_energy(self) -> None:
        """The floor changes when charging happens, never how much is needed."""
        without = compute_requirement(
            planning_input(current_soc_pct=20.0, reserve_floor_pct=0.0)
        )
        with_floor = compute_requirement(
            planning_input(current_soc_pct=20.0, reserve_floor_pct=30.0)
        )
        assert without.grid_energy_required_kwh == with_floor.grid_energy_required_kwh

    def test_floor_of_zero_reproduces_deadline_driven_plan(self) -> None:
        """V1 carries the floor without acting on it; the window must be identical."""
        prices = hourly(at(0), prices_with_cheap_pair(20))
        baseline = plan_charging(
            planning_input(
                price_intervals=prices, current_soc_pct=20.0, reserve_floor_pct=0.0
            )
        )
        with_floor = plan_charging(
            planning_input(
                price_intervals=prices, current_soc_pct=20.0, reserve_floor_pct=30.0
            )
        )
        assert with_floor.start == baseline.start
        assert with_floor.end == baseline.end
        assert with_floor.estimated_cost == baseline.estimated_cost
        assert with_floor.urgency is ChargeUrgency.URGENT
        assert baseline.urgency is ChargeUrgency.NORMAL


class TestNoChargeNeeded:
    """A car at target produces a plan with no window."""

    def test_plan_has_no_window(self) -> None:
        plan = plan_charging(
            planning_input(current_soc_pct=80.0, price_intervals=hourly(at(0), FLAT_24))
        )
        assert not plan.is_charge_needed
        assert not plan.has_window
        assert plan.start is None
        assert plan.estimated_cost is None
        assert plan.can_meet_target
        assert plan.shortfall_kwh == 0.0
        assert plan.estimated_soc_at_end == 80.0


class TestCheapestWindow:
    """Window selection."""

    @pytest.mark.parametrize("index", [0, 11, 22])
    def test_finds_cheapest_pair_anywhere_in_horizon(self, index: int) -> None:
        plan = plan_charging(
            planning_input(price_intervals=hourly(at(0), prices_with_cheap_pair(index)))
        )
        assert plan.start == at(index)
        assert plan.end == at(index) + timedelta(hours=2)
        assert plan.can_meet_target
        assert plan.estimated_cost == Decimal("2.0")

    def test_equal_prices_pick_earliest_window(self) -> None:
        """Ties must resolve deterministically or approval churns."""
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
        assert plan.start == at(0)

    def test_two_equally_cheap_windows_pick_earliest(self) -> None:
        prices = list(FLAT_24)
        prices[3] = prices[4] = "0.5"
        prices[15] = prices[16] = "0.5"
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), prices)))
        assert plan.start == at(3)

    def test_not_before_excludes_earlier_cheap_window(self) -> None:
        plan = plan_charging(
            planning_input(
                price_intervals=hourly(at(0), prices_with_cheap_pair(2)),
                not_before=at(10),
            )
        )
        assert plan.start >= at(10)

    def test_window_crosses_midnight(self) -> None:
        prices = ["2.0"] * 12
        prices[3] = prices[4] = "0.1"  # 23:00 and 00:00
        plan = plan_charging(
            planning_input(
                now=at(20),
                ready_by=at(8, day=11),
                price_intervals=hourly(at(20), prices),
            )
        )
        assert plan.start == at(23)
        assert plan.end == at(1, day=11)
        assert plan.can_meet_target

    def test_negative_prices_are_preferred(self) -> None:
        prices = list(FLAT_24)
        prices[7] = prices[8] = "-0.5"
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), prices)))
        assert plan.start == at(7)
        assert plan.estimated_cost == Decimal("-10.0")

    def test_very_high_prices_do_not_break_selection(self) -> None:
        prices = ["99999.999999"] * 24
        prices[5] = prices[6] = "99999.000000"
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), prices)))
        assert plan.start == at(5)


class TestIntervalLengths:
    """Hourly and 15-minute resolutions."""

    def test_hourly_prices(self) -> None:
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
        assert len(plan.intervals) == 2
        assert plan.duration_hours == 2.0

    def test_quarter_hourly_prices(self) -> None:
        plan = plan_charging(
            planning_input(price_intervals=quarter_hourly(at(0), ["1.0"] * 48))
        )
        assert len(plan.intervals) == 8
        assert plan.duration_hours == 2.0
        assert plan.planned_grid_kwh == pytest.approx(20.0)

    def test_quarter_hourly_allows_finer_window_placement(self) -> None:
        prices = ["2.0"] * 48
        for i in (5, 6, 7, 8, 9, 10, 11, 12):
            prices[i] = "0.1"
        plan = plan_charging(
            planning_input(price_intervals=quarter_hourly(at(0), prices))
        )
        assert plan.start == at(1, 15)


class TestPartialFinalInterval:
    """V1 allocates whole intervals, and must report the resulting slack."""

    def test_over_allocation_is_reported(self) -> None:
        """22.22 kWh needs 2.22 hours, so three whole hours are booked."""
        plan = plan_charging(
            planning_input(
                charging_efficiency=0.9, price_intervals=hourly(at(0), FLAT_24)
            )
        )
        assert len(plan.intervals) == 3
        assert plan.allocated_grid_kwh == pytest.approx(30.0)
        assert plan.planned_grid_kwh == pytest.approx(20.0 / 0.9)
        assert plan.over_allocation_kwh == pytest.approx(30.0 - 20.0 / 0.9)

    def test_cost_reflects_energy_drawn_not_window_length(self) -> None:
        """The car stops at target, so cost must not bill the unused slack."""
        plan = plan_charging(
            planning_input(
                charging_efficiency=0.9,
                price_intervals=hourly(at(0), ["1.0"] * 24),
            )
        )
        assert plan.estimated_cost == pytest.approx(Decimal(str(20.0 / 0.9)))

    def test_exact_fit_has_no_over_allocation(self) -> None:
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
        assert plan.over_allocation_kwh == pytest.approx(0.0)


class TestGapsAndHorizon:
    """Missing prices must never be treated as free."""

    def test_gap_breaks_contiguity(self) -> None:
        early = hourly(at(0), ["0.1", "0.1"])
        late = hourly(at(5), ["1.0", "1.0"])
        plan = plan_charging(planning_input(price_intervals=early + late))
        assert plan.start == at(0)
        assert plan.can_meet_target

    def test_window_cannot_span_a_gap(self) -> None:
        """Two cheap hours either side of a gap cannot be combined."""
        first = hourly(at(0), ["0.1"])
        second = hourly(at(3), ["0.1"])
        expensive = hourly(at(8), ["5.0", "5.0"])
        plan = plan_charging(planning_input(price_intervals=first + second + expensive))
        assert plan.start == at(8)
        assert plan.can_meet_target

    def test_insufficient_horizon_returns_best_effort(self) -> None:
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), ["1.0"])))
        assert not plan.can_meet_target
        assert plan.planned_grid_kwh == pytest.approx(10.0)
        assert plan.shortfall_kwh == pytest.approx(10.0)
        assert plan.estimated_soc_at_end == pytest.approx(70.0)

    def test_no_prices_at_all(self) -> None:
        plan = plan_charging(planning_input(price_intervals=()))
        assert not plan.can_meet_target
        assert not plan.has_window
        assert plan.shortfall_kwh == pytest.approx(20.0)
        assert plan.estimated_cost is None

    def test_prices_entirely_in_the_past_are_ignored(self) -> None:
        past = hourly(at(0, day=9), ["0.1"] * 24)
        plan = plan_charging(planning_input(price_intervals=past))
        assert not plan.has_window


class TestImpossibleDeadline:
    """Never pretend the target can be met."""

    def test_shortfall_is_explicit(self) -> None:
        plan = plan_charging(
            planning_input(ready_by=at(1), price_intervals=hourly(at(0), ["1.0"] * 24))
        )
        assert not plan.can_meet_target
        assert plan.planned_grid_kwh == pytest.approx(10.0)
        assert plan.shortfall_kwh == pytest.approx(10.0)
        assert plan.estimated_soc_at_end == pytest.approx(70.0)

    def test_best_effort_prefers_cheaper_run_when_energy_ties(self) -> None:
        cheap = hourly(at(0), ["0.1"])
        expensive = hourly(at(5), ["9.0"])
        plan = plan_charging(
            planning_input(ready_by=at(6), price_intervals=cheap + expensive)
        )
        assert plan.start == at(0)
        assert not plan.can_meet_target

    def test_deadline_before_now_yields_no_window(self) -> None:
        plan = plan_charging(
            planning_input(
                now=at(12), ready_by=at(6), price_intervals=hourly(at(0), FLAT_24)
            )
        )
        assert not plan.has_window
        assert not plan.can_meet_target


class TestPriceQuality:
    """Forecast and actual prices are distinguished."""

    def test_all_actual(self) -> None:
        plan = plan_charging(
            planning_input(price_intervals=hourly(at(0), FLAT_24, PriceQuality.ACTUAL))
        )
        assert plan.price_quality is PlanPriceQuality.ACTUAL

    def test_all_forecast(self) -> None:
        plan = plan_charging(
            planning_input(
                price_intervals=hourly(at(0), FLAT_24, PriceQuality.FORECAST)
            )
        )
        assert plan.price_quality is PlanPriceQuality.FORECAST

    def test_mixed_horizon(self) -> None:
        actual = hourly(at(0), ["5.0"], PriceQuality.ACTUAL)
        forecast = hourly(at(1), ["5.0"], PriceQuality.FORECAST)
        plan = plan_charging(planning_input(price_intervals=actual + forecast))
        assert plan.price_quality is PlanPriceQuality.MIXED


class TestInputHandling:
    """Input ordering and overlap."""

    def test_unsorted_intervals_are_sorted(self) -> None:
        ordered = hourly(at(0), prices_with_cheap_pair(6))
        plan = plan_charging(planning_input(price_intervals=tuple(reversed(ordered))))
        assert plan.start == at(6)

    def test_overlapping_intervals_are_rejected(self) -> None:
        first = PriceInterval(at(0), at(2), Decimal("1.0"))
        second = PriceInterval(at(1), at(3), Decimal("1.0"))
        with pytest.raises(InvalidPlanningInput, match="overlap"):
            plan_charging(planning_input(price_intervals=(first, second)))

    def test_touching_intervals_are_not_overlapping(self) -> None:
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
        assert plan.can_meet_target


class TestDeterminism:
    """Identical inputs must produce identical plans."""

    def test_same_input_gives_same_plan_id(self) -> None:
        data = planning_input(price_intervals=hourly(at(0), prices_with_cheap_pair(9)))
        assert plan_charging(data).id == plan_charging(data).id

    def test_different_window_gives_different_plan_id(self) -> None:
        first = plan_charging(
            planning_input(price_intervals=hourly(at(0), prices_with_cheap_pair(9)))
        )
        second = plan_charging(
            planning_input(price_intervals=hourly(at(0), prices_with_cheap_pair(14)))
        )
        assert first.id != second.id


class TestNumericPrecision:
    """Currency is summed as Decimal, not float."""

    def test_repeated_addition_stays_exact(self) -> None:
        """Three lots of 0.1 must be 0.3, which float addition does not give."""
        plan = plan_charging(
            planning_input(
                current_soc_pct=97.0,
                target_soc_pct=100.0,
                charging_power_kw=1.0,
                price_intervals=hourly(at(0), ["0.1"] * 5),
            )
        )
        assert plan.estimated_cost == Decimal("0.3")
        assert 0.1 + 0.1 + 0.1 != 0.3  # the float result this avoids

    def test_cost_is_a_decimal(self) -> None:
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
        assert isinstance(plan.estimated_cost, Decimal)


class TestDaylightSaving:
    """Durations come from elapsed time, never wall-clock arithmetic."""

    def test_spring_forward_day_has_23_hours(self) -> None:
        intervals = hourly(at(0, day=29, month=3), ["1.0"] * 23)
        assert intervals[-1].end == at(0, day=30, month=3)

    def test_window_spanning_spring_forward_reports_elapsed_hours(self) -> None:
        prices = ["9.0"] * 23
        prices[1] = prices[2] = "0.1"
        plan = plan_charging(
            planning_input(
                now=at(0, day=29, month=3),
                ready_by=at(0, day=30, month=3),
                price_intervals=hourly(at(0, day=29, month=3), prices),
            )
        )
        assert plan.start == at(1, day=29, month=3)
        # Wall clock says three hours because 02:00 never happens.
        assert plan.end.hour - plan.start.hour == 3
        assert plan.duration_hours == 2.0
        assert plan.planned_grid_kwh == pytest.approx(20.0)

    def test_fall_back_day_has_25_hours(self) -> None:
        intervals = hourly(at(0, day=25, month=10), ["1.0"] * 25)
        assert intervals[-1].end == at(0, day=26, month=10)

    def test_window_spanning_fall_back_delivers_correct_energy(self) -> None:
        prices = ["9.0"] * 25
        prices[2] = prices[3] = "0.1"
        plan = plan_charging(
            planning_input(
                now=at(0, day=25, month=10),
                ready_by=at(0, day=26, month=10),
                price_intervals=hourly(at(0, day=25, month=10), prices),
            )
        )
        assert plan.duration_hours == 2.0
        assert plan.planned_grid_kwh == pytest.approx(20.0)

    def test_repeated_local_hour_is_not_treated_as_a_gap(self) -> None:
        """The ambiguous 02:00 hour occurs twice; both must stay contiguous."""
        intervals = hourly(at(0, day=25, month=10), ["1.0"] * 25)
        for previous, current in pairwise(intervals):
            assert to_utc(current.start) == to_utc(previous.end)
        # The ambiguous hour appears twice with different UTC offsets.
        offsets = [iv.start.utcoffset() for iv in intervals]
        assert len(set(offsets)) == 2


class TestEstimatedSoc:
    """Estimated end state of charge."""

    def test_meeting_target_lands_on_target(self) -> None:
        plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
        assert plan.estimated_soc_at_end == pytest.approx(80.0)

    def test_efficiency_is_applied_to_delivered_energy(self) -> None:
        plan = plan_charging(
            planning_input(
                charging_efficiency=0.9, price_intervals=hourly(at(0), FLAT_24)
            )
        )
        assert plan.estimated_soc_at_end == pytest.approx(80.0)

    def test_never_exceeds_100_percent(self) -> None:
        plan = plan_charging(
            planning_input(
                current_soc_pct=0.0,
                target_soc_pct=100.0,
                price_intervals=hourly(at(0), FLAT_24),
            )
        )
        assert plan.estimated_soc_at_end <= 100.0


def test_planner_uses_only_aware_datetimes() -> None:
    """A plan must never leak a naive datetime into the rest of the system."""
    plan = plan_charging(planning_input(price_intervals=hourly(at(0), FLAT_24)))
    assert plan.start is not None
    assert plan.start.tzinfo is not None
    assert plan.end.tzinfo is not None
    assert plan.created_at.tzinfo is not None
    assert plan.start.tzinfo is CPH or plan.start.utcoffset() is not None
