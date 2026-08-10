"""Tests for the one-sentence summary.

The sentence is the first thing a user reads, so these assert on wording as well
as on which branch is taken: a sentence that is technically correct and reads
badly is the bug this whole pass exists to fix.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from custom_components.bitcruise.models import (
    ChargePlan,
    ChargeRequirement,
    ChargeUrgency,
    PlanSource,
    PlanStatus,
)
from custom_components.bitcruise.planner import compute_requirement, plan_charging
from custom_components.bitcruise.summary import MAX_STATE_LENGTH, summarize

from .builders import at, hourly, planning_input, prices_with_cheap_pair

# A plan charging over the two cheap hours starting at 03:00 on 11 August.
CHEAP_HOUR = 3


def plan_tomorrow_morning() -> ChargePlan:
    """Build a plan whose window falls in the small hours of the next day."""
    return plan_charging(
        planning_input(
            now=at(20),
            ready_by=at(7, day=11),
            price_intervals=hourly(
                at(20),
                prices_with_cheap_pair(7),  # 03:00-05:00 next day
            ),
        )
    )


def requirement_for(**overrides: object) -> ChargeRequirement:
    """Build the requirement matching the default planning input."""
    return compute_requirement(planning_input(**overrides))


class TestApprovedAndPending:
    """States that have a window to describe."""

    def test_approved_reads_as_a_sentence(self):
        plan = plan_tomorrow_morning()
        text = summarize(
            status=PlanStatus.APPROVED,
            now=at(20),
            plan=plan,
            currency="DKK",
        )
        assert text == "Charging 03:00-05:00 tomorrow, 20.0 kWh for 2.00 DKK."

    def test_awaiting_approval_says_what_changed(self):
        plan = plan_tomorrow_morning()
        text = summarize(
            status=PlanStatus.AWAITING_APPROVAL,
            now=at(20),
            plan=plan,
            currency="DKK",
            proposal_reason=PlanSource.PRICE_UPDATE,
        )
        assert text.startswith("Prices changed: approve charging 03:00-05:00 tomorrow")

    def test_a_replacement_names_both_ends_of_the_move(self):
        """Move it to 03:00 is unanswerable without saying from where."""
        moving_to = plan_tomorrow_morning()
        approved = plan_charging(
            planning_input(
                now=at(20),
                ready_by=at(7, day=11),
                price_intervals=hourly(at(20), prices_with_cheap_pair(2)),
            )
        )
        text = summarize(
            status=PlanStatus.AWAITING_APPROVAL,
            now=at(20),
            plan=moving_to,
            currency="DKK",
            proposal_reason=PlanSource.PRICE_UPDATE,
            replaces=approved,
        )

        assert "approve moving charging from 22:00-00:00 today" in text
        assert "to 03:00-05:00 tomorrow" in text

    def test_a_first_plan_is_not_described_as_a_move(self):
        plan = plan_tomorrow_morning()
        text = summarize(
            status=PlanStatus.AWAITING_APPROVAL,
            now=at(20),
            plan=plan,
            proposal_reason=PlanSource.INITIAL,
        )
        assert "approve charging 03:00-05:00 tomorrow" in text
        assert "moving" not in text

    def test_a_window_later_in_the_week_is_dated(self):
        prices = ["2.0"] * 60
        prices[30] = prices[31] = "0.1"  # 02:00 on 12 August
        plan = plan_charging(
            planning_input(
                now=at(20),
                ready_by=at(7, day=13),
                price_intervals=hourly(at(20), prices),
            )
        )
        text = summarize(status=PlanStatus.APPROVED, now=at(20), plan=plan)
        assert "Wed 12 Aug" in text

    def test_cost_is_dropped_when_no_currency_is_known(self):
        """An unlabelled number invites the reader to supply their own unit."""
        plan = plan_tomorrow_morning()
        text = summarize(status=PlanStatus.APPROVED, now=at(20), plan=plan)
        assert text == "Charging 03:00-05:00 tomorrow, 20.0 kWh."

    def test_cost_is_rounded_to_currency_precision(self):
        plan = plan_charging(
            planning_input(
                now=at(20),
                ready_by=at(7, day=11),
                price_intervals=hourly(
                    at(20), prices_with_cheap_pair(7, cheap="0.123456789")
                ),
            )
        )
        text = summarize(
            status=PlanStatus.APPROVED, now=at(20), plan=plan, currency="DKK"
        )
        assert "for 2.47 DKK" in text


class TestNothingScheduled:
    """States with no window, which must not all read the same."""

    def test_waiting_for_prices(self):
        text = summarize(
            status=PlanStatus.NEEDS_CHARGE,
            now=at(20),
            plan=None,
            requirement=requirement_for(),
        )
        assert text == "Needs 20.0 kWh from the grid; waiting for electricity prices."

    def test_no_window_fits_before_the_deadline(self):
        """Prices exist, but none of them land before the deadline."""
        plan = plan_charging(
            planning_input(
                now=at(6, day=11),
                ready_by=at(7, day=11),
                price_intervals=hourly(at(8, day=11), ["2.0"] * 12),
            )
        )
        assert not plan.has_window
        text = summarize(
            status=PlanStatus.NEEDS_CHARGE,
            now=at(6, day=11),
            plan=plan,
            requirement=requirement_for(),
            ready_by=at(7, day=11),
        )
        assert text.endswith("no window fits before 07:00 today.")

    def test_an_unapproved_window_says_so(self):
        """A rejected plan still exists; the sentence must not imply charging."""
        plan = plan_tomorrow_morning()
        text = summarize(
            status=PlanStatus.NEEDS_CHARGE,
            now=at(20),
            plan=plan,
            requirement=requirement_for(),
            currency="DKK",
        )
        assert text.endswith("is not approved.")
        assert "03:00-05:00 tomorrow" in text

    def test_idle_names_the_battery_level(self):
        text = summarize(
            status=PlanStatus.IDLE,
            now=at(20),
            current_soc_pct=91.4,
            target_soc_pct=90.0,
        )
        assert text == "No charging needed; battery is at 91% of a 90% target."

    def test_smart_charging_off_says_nothing_is_being_planned(self):
        text = summarize(
            status=PlanStatus.IDLE,
            now=at(20),
            smart_charging=False,
            current_soc_pct=47.0,
            target_soc_pct=90.0,
        )
        assert text == "Smart charging is off; BitCruise is not planning anything."


class TestProblems:
    """The error case has to name the fault, not announce that there is one."""

    def test_the_first_problem_is_named(self):
        text = summarize(
            status=PlanStatus.ERROR,
            now=at(20),
            problems=("sensor.car_battery: state is unavailable",),
        )
        assert text == "Cannot plan: sensor.car_battery: state is unavailable."

    def test_further_problems_are_counted(self):
        text = summarize(
            status=PlanStatus.ERROR,
            now=at(20),
            problems=("first problem", "second", "third"),
        )
        assert text == "Cannot plan: first problem (+2 more)."

    def test_an_error_with_no_stated_problem_still_says_something(self):
        text = summarize(status=PlanStatus.ERROR, now=at(20))
        assert text == "Cannot plan: no usable input."

    def test_a_long_problem_is_clipped_to_the_state_limit(self):
        """Over 255 characters and Home Assistant rejects the state outright."""
        text = summarize(
            status=PlanStatus.ERROR,
            now=at(20),
            problems=("x" * 400,),
        )
        assert len(text) <= MAX_STATE_LENGTH
        assert text.endswith("…")


class TestWindowFormatting:
    """Window rendering, which is where a plausible sentence goes quietly wrong."""

    @pytest.mark.parametrize(
        ("hours_ahead", "expected"),
        [(0, "today"), (6, "tomorrow")],
    )
    def test_day_label_follows_the_calendar_not_the_evening(
        self, hours_ahead: int, expected: str
    ):
        """A window at 02:00 is 'tonight' in speech and tomorrow on a calendar."""
        start = at(20) + timedelta(hours=hours_ahead)
        plan = ChargePlan(
            id="test",
            created_at=at(20),
            start=start,
            end=start + timedelta(hours=1),
            current_soc_pct=50.0,
            target_soc_pct=80.0,
            reserve_floor_pct=0.0,
            required_battery_kwh=10.0,
            required_grid_kwh=10.0,
            planned_grid_kwh=10.0,
            allocated_grid_kwh=10.0,
            estimated_soc_at_end=80.0,
            estimated_cost=Decimal("1.00"),
            can_meet_target=True,
            shortfall_kwh=0.0,
            price_quality=None,
            urgency=ChargeUrgency.NORMAL,
            below_reserve_floor=False,
        )
        text = summarize(status=PlanStatus.APPROVED, now=at(20), plan=plan)
        assert expected in text
