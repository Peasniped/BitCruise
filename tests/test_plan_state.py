"""Tests for the proposal/approval state machine.

Every rule in DESIGN.md section 7 gets a test here, plus the guarantee those
rules exist to protect: an approved plan is never silently changed (ADR-003).
"""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from custom_components.bitcruise.models import (
    ApprovalPolicy,
    ChargePlan,
    PlanSource,
    PriceQuality,
)
from custom_components.bitcruise.plan_state import (
    PlanInputs,
    PlanRecord,
    StoredState,
    accept,
    clear_rejection,
    price_fingerprint,
    proposal_reason,
    reconcile,
    reject,
    stored_state_from_dict,
    stored_state_to_dict,
    windows_equivalent,
)
from custom_components.bitcruise.planner import plan_charging

from .builders import at, hourly, planning_input, prices_with_cheap_pair

ONE_INTERVAL = timedelta(hours=1)


def plan_at(cheap_hour: int, **overrides: object) -> ChargePlan:
    """Build a plan whose window sits on a chosen cheap pair of hours."""
    return plan_charging(
        planning_input(
            price_intervals=hourly(at(0), prices_with_cheap_pair(cheap_hour)),
            **overrides,
        )
    )


def settled(policy: ApprovalPolicy = ApprovalPolicy.ALWAYS_ASK) -> PlanRecord:
    """Build a record with one plan approved, the way a user would reach it."""
    proposed = reconcile(
        PlanRecord(),
        plan_at(9),
        now=at(0),
        policy=policy,
        material_change=ONE_INTERVAL,
        reason=PlanSource.INITIAL,
    )
    return accept(proposed) if proposed.proposal else proposed


def fold(
    record: PlanRecord,
    candidate: ChargePlan | None,
    *,
    policy: ApprovalPolicy = ApprovalPolicy.ALWAYS_ASK,
    **overrides: object,
) -> PlanRecord:
    """Run one reconciliation with the usual arguments."""
    kwargs: dict[str, object] = {
        "now": at(0),
        "policy": policy,
        "material_change": ONE_INTERVAL,
        "reason": PlanSource.PRICE_UPDATE,
    }
    kwargs.update(overrides)
    return reconcile(record, candidate, **kwargs)  # type: ignore[arg-type]


class TestFirstPlan:
    """A new plan when nothing is approved yet."""

    def test_always_ask_proposes(self) -> None:
        record = fold(PlanRecord(), plan_at(9), reason=PlanSource.INITIAL)
        assert record.proposal is not None
        assert record.approved is None
        assert record.proposal_reason is PlanSource.INITIAL
        assert record.requires_approval

    def test_ask_on_change_approves_without_prompting(self) -> None:
        record = fold(PlanRecord(), plan_at(9), policy=ApprovalPolicy.ASK_ON_CHANGE)
        assert record.approved is not None
        assert record.proposal is None
        assert record.requires_approval is False

    def test_a_plan_with_no_window_is_not_proposed(self) -> None:
        """Nothing to approve when the optimizer found no window."""
        no_window = plan_charging(planning_input(price_intervals=()))
        assert not no_window.has_window
        assert fold(PlanRecord(), no_window).proposal is None


class TestAcceptAndReject:
    """The two answers a user can give."""

    def test_accept_promotes_atomically(self) -> None:
        record = accept(fold(PlanRecord(), plan_at(9)))
        assert record.approved is not None
        assert record.proposal is None
        assert record.proposal_reason is None

    def test_reject_clears_the_proposal(self) -> None:
        record = reject(fold(PlanRecord(), plan_at(9)))
        assert record.proposal is None
        assert record.approved is None

    def test_a_rejected_plan_is_not_proposed_again(self) -> None:
        """The next evaluation is seconds later and produces the same window."""
        rejected = reject(fold(PlanRecord(), plan_at(9)))
        assert fold(rejected, plan_at(9)).proposal is None

    def test_a_different_plan_is_still_proposed_after_a_rejection(self) -> None:
        rejected = reject(fold(PlanRecord(), plan_at(9)))
        assert fold(rejected, plan_at(14)).proposal is not None

    def test_recalculate_reconsiders_a_rejected_window(self) -> None:
        rejected = reject(fold(PlanRecord(), plan_at(9)))
        reconsidered = fold(clear_rejection(rejected), plan_at(9))
        assert reconsidered.proposal is not None

    def test_accepting_nothing_changes_nothing(self) -> None:
        assert accept(PlanRecord()) == PlanRecord()

    def test_rejecting_nothing_changes_nothing(self) -> None:
        approved = settled()
        assert reject(approved) == approved


class TestReplanAgainstAnApprovedPlan:
    """The rules that protect an approved plan from being moved silently."""

    def test_equivalent_replan_keeps_the_approved_plan(self) -> None:
        approved = settled()
        after = fold(approved, plan_at(9))
        assert after.approved == approved.approved
        assert after.proposal is None
        assert after.requires_approval is False

    def test_material_change_stages_a_replacement(self) -> None:
        approved = settled()
        after = fold(approved, plan_at(14))

        assert after.approved == approved.approved
        assert after.proposal is not None
        assert after.is_replacement

    def test_accepting_the_move_replaces_atomically(self) -> None:
        moved = accept(fold(settled(), plan_at(14)))
        assert moved.proposal is None
        assert moved.approved is not None
        assert moved.approved.start == plan_at(14).start

    def test_keeping_the_old_plan_discards_the_replacement(self) -> None:
        approved = settled()
        kept = reject(fold(approved, plan_at(14)))

        assert kept.approved == approved.approved
        assert kept.proposal is None

    def test_a_reverted_price_curve_withdraws_the_replacement(self) -> None:
        """Prices moved the window, then moved it back before anyone answered."""
        staged = fold(settled(), plan_at(14))
        assert staged.proposal is not None

        reverted = fold(staged, plan_at(9))
        assert reverted.proposal is None
        assert reverted.approved is not None

    def test_ask_on_change_still_asks_about_a_move(self) -> None:
        """The whole point of the policy: the first plan is free, moves are not."""
        approved = settled(ApprovalPolicy.ASK_ON_CHANGE)
        after = fold(approved, plan_at(14), policy=ApprovalPolicy.ASK_ON_CHANGE)

        assert after.approved == approved.approved
        assert after.proposal is not None


class TestUnusableInputs:
    """A blink from a source entity must not destroy state."""

    def test_a_pending_proposal_survives(self) -> None:
        pending = fold(PlanRecord(), plan_at(9))
        after = fold(pending, None, inputs_usable=False)
        assert after.proposal == pending.proposal

    def test_an_approved_plan_survives(self) -> None:
        approved = settled()
        assert fold(approved, None, inputs_usable=False).approved is not None

    def test_no_charge_needed_keeps_the_approved_plan(self) -> None:
        """Withdrawing it here is exactly the silent change ADR-003 forbids."""
        approved = settled()
        after = fold(approved, None)
        assert after.approved == approved.approved
        assert after.proposal is None


class TestExpiry:
    """Last night's approval must not block tonight's plan."""

    def test_a_finished_window_is_retired(self) -> None:
        approved = settled()
        after = fold(approved, None, now=at(0, day=12))

        assert after.approved is None
        assert after.completed_plan_id == approved.approved.id

    def test_a_window_still_running_is_not_retired(self) -> None:
        approved = settled()
        assert approved.approved.start is not None
        after = fold(approved, None, now=approved.approved.start)
        assert after.approved is not None

    def test_the_completed_plan_is_not_immediately_re_proposed(self) -> None:
        expired = fold(settled(), None, now=at(0, day=12))
        again = fold(expired, plan_at(9), now=at(0, day=12))
        assert again.proposal is None

    def test_a_new_window_is_proposed_after_expiry(self) -> None:
        expired = fold(settled(), None, now=at(0, day=12))
        again = fold(expired, plan_at(14), now=at(0, day=12))
        assert again.proposal is not None


class TestSmartChargingOff:
    """Turning the switch off means BitCruise is not deciding anything."""

    def test_everything_is_cleared(self) -> None:
        approved = settled()
        assert fold(approved, plan_at(9), smart_charging=False) == PlanRecord()

    def test_nothing_is_proposed_while_off(self) -> None:
        record = fold(PlanRecord(), plan_at(9), smart_charging=False)
        assert record.proposal is None
        assert record.approved is None


class TestAutomaticPolicy:
    """Never ask. The one deliberate relaxation of ADR-003, chosen by the user."""

    AUTOMATIC = ApprovalPolicy.AUTOMATIC

    def test_a_first_plan_is_approved_without_asking(self) -> None:
        record = fold(PlanRecord(), plan_at(9), policy=self.AUTOMATIC)
        assert record.approved is not None
        assert record.proposal is None

    def test_a_moved_window_replaces_the_approved_plan(self) -> None:
        """Under any other policy this stages a proposal and waits."""
        approved = fold(PlanRecord(), plan_at(9), policy=self.AUTOMATIC)
        moved = plan_at(3)

        record = fold(approved, moved, policy=self.AUTOMATIC)

        assert record.approved.id == moved.id
        assert record.proposal is None
        assert record.requires_approval is False

    def test_an_unchanged_window_is_left_alone(self) -> None:
        approved = fold(PlanRecord(), plan_at(9), policy=self.AUTOMATIC)
        record = fold(approved, plan_at(9), policy=self.AUTOMATIC)
        assert record == approved

    def test_a_rejection_from_another_policy_is_ignored(self) -> None:
        """A stale rejection would suppress the window with nothing to explain it."""
        rejected = reject(fold(PlanRecord(), plan_at(9)))
        assert rejected.rejected_plan_id is not None

        record = fold(rejected, plan_at(9), policy=self.AUTOMATIC)

        assert record.approved is not None
        assert record.approved.id == rejected.rejected_plan_id

    def test_a_completed_window_is_still_not_re_approved(self) -> None:
        """Last night's finished window is wrong under every policy."""
        approved = fold(PlanRecord(), plan_at(9), policy=self.AUTOMATIC)
        expired = fold(approved, None, policy=self.AUTOMATIC, now=at(23))
        assert expired.completed_plan_id is not None

        record = fold(expired, plan_at(9), policy=self.AUTOMATIC, now=at(23))

        assert record.approved is None

    def test_switching_to_automatic_answers_a_pending_question(self) -> None:
        """A proposal nothing will ever answer must not sit there forever."""
        pending = fold(settled(), plan_at(3))
        assert pending.requires_approval is True

        record = fold(pending, plan_at(3), policy=self.AUTOMATIC)

        assert record.requires_approval is False
        assert record.approved.id == plan_at(3).id


class TestWindowsEquivalent:
    """What counts as the same window."""

    def test_identical_windows_match(self) -> None:
        assert windows_equivalent(plan_at(9), plan_at(9), ONE_INTERVAL)

    def test_a_whole_interval_of_movement_is_material(self) -> None:
        """Strict at the boundary, so hourly prices always prompt on a real move."""
        early = plan_at(9)
        late = plan_at(10)
        assert late.start is not None and early.start is not None
        assert late.start - early.start == ONE_INTERVAL
        assert not windows_equivalent(early, late, ONE_INTERVAL)

    def test_movement_within_the_tolerance_is_not(self) -> None:
        assert windows_equivalent(plan_at(9), plan_at(10), timedelta(hours=2))

    def test_none_matches_only_none(self) -> None:
        assert windows_equivalent(None, None, ONE_INTERVAL)
        assert not windows_equivalent(plan_at(9), None, ONE_INTERVAL)


class TestProposalReason:
    """Explaining why a window moved."""

    BASE = PlanInputs(
        current_soc_pct=50.0,
        target_soc_pct=80.0,
        usable_capacity_kwh=80.0,
        charging_power_kw=11.0,
        ready_by=at(0, day=11),
        prices="abc",
    )

    def test_first_ever_evaluation(self) -> None:
        assert proposal_reason(None, self.BASE) is PlanSource.INITIAL

    def test_manual_wins_over_everything(self) -> None:
        assert proposal_reason(None, self.BASE, manual=True) is PlanSource.MANUAL

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("current_soc_pct", 42.0, PlanSource.SOC_CHANGE),
            ("prices", "xyz", PlanSource.PRICE_UPDATE),
            ("target_soc_pct", 90.0, PlanSource.SETTINGS_CHANGE),
            ("usable_capacity_kwh", 75.0, PlanSource.SETTINGS_CHANGE),
            ("charging_power_kw", 3.7, PlanSource.SETTINGS_CHANGE),
            ("ready_by", at(6, day=11), PlanSource.SETTINGS_CHANGE),
        ],
    )
    def test_each_input(self, field: str, value: object, expected: PlanSource) -> None:
        from dataclasses import replace

        assert proposal_reason(self.BASE, replace(self.BASE, **{field: value})) is (
            expected
        )

    def test_nothing_changed_means_the_clock_moved(self) -> None:
        assert proposal_reason(self.BASE, self.BASE) is PlanSource.SCHEDULE


class TestPriceFingerprint:
    """Recognising a price curve that only looks new."""

    CURVE = hourly(at(0), prices_with_cheap_pair(9))

    def test_republished_identical_curve_matches(self) -> None:
        assert price_fingerprint(self.CURVE) == price_fingerprint(list(self.CURVE))

    def test_a_changed_price_does_not(self) -> None:
        assert price_fingerprint(self.CURVE) != price_fingerprint(
            hourly(at(0), prices_with_cheap_pair(14))
        )

    def test_quality_is_part_of_the_curve(self) -> None:
        """A forecast replaced by the settled price is a real change."""
        from dataclasses import replace

        promoted = tuple(
            replace(interval, quality=PriceQuality.FORECAST) for interval in self.CURVE
        )
        assert price_fingerprint(self.CURVE) != price_fingerprint(promoted)

    def test_empty_curve_is_stable(self) -> None:
        assert price_fingerprint([]) == price_fingerprint(())


class TestPersistence:
    """The record has to survive a restart, and survive a corrupt store."""

    def test_round_trip_preserves_an_approved_plan(self) -> None:
        approved = settled()
        restored = stored_state_from_dict(
            stored_state_to_dict(StoredState(record=approved))
        )

        assert restored.smart_charging is True
        assert restored.record.approved is not None
        assert restored.record.approved.id == approved.approved.id
        assert restored.record.approved.start == approved.approved.start
        assert restored.record.approved.end == approved.approved.end

    def test_round_trip_preserves_cost_exactly(self) -> None:
        """Cost is Decimal; a float round trip would quietly lose precision."""
        approved = settled()
        restored = stored_state_from_dict(
            stored_state_to_dict(StoredState(record=approved))
        ).record
        assert restored.approved.estimated_cost == approved.approved.estimated_cost
        assert isinstance(restored.approved.estimated_cost, Decimal)

    def test_round_trip_preserves_a_pending_proposal(self) -> None:
        pending = fold(PlanRecord(), plan_at(9), reason=PlanSource.SOC_CHANGE)
        restored = stored_state_from_dict(
            stored_state_to_dict(StoredState(record=pending))
        ).record
        assert restored.proposal is not None
        assert restored.proposal_reason is PlanSource.SOC_CHANGE

    def test_round_trip_preserves_the_rejection(self) -> None:
        rejected = reject(fold(PlanRecord(), plan_at(9)))
        restored = stored_state_from_dict(
            stored_state_to_dict(StoredState(record=rejected))
        ).record
        assert restored.rejected_plan_id == rejected.rejected_plan_id

    def test_smart_charging_off_survives(self) -> None:
        restored = stored_state_from_dict(
            stored_state_to_dict(StoredState(smart_charging=False))
        )
        assert restored.smart_charging is False

    def test_the_approval_policy_survives(self) -> None:
        """It is a control now, so it cannot be re-read from the config entry."""
        restored = stored_state_from_dict(
            stored_state_to_dict(StoredState(approval_policy=ApprovalPolicy.AUTOMATIC))
        )
        assert restored.approval_policy is ApprovalPolicy.AUTOMATIC

    def test_a_store_predating_the_policy_reports_none(self) -> None:
        """None means fall back to the config entry, not use the default."""
        assert stored_state_from_dict({"smart_charging": True}).approval_policy is None

    def test_an_unknown_policy_reports_none(self) -> None:
        payload = stored_state_to_dict(StoredState())
        payload["approval_policy"] = "invented_by_a_later_version"
        assert stored_state_from_dict(payload).approval_policy is None

    def test_the_payload_is_json_safe(self) -> None:
        """HA storage writes JSON; a Decimal or datetime in there would raise."""
        json.dumps(stored_state_to_dict(StoredState(record=settled())))

    @pytest.mark.parametrize(
        "payload",
        [None, "nonsense", [], {"approved": "not a plan"}, {"approved": {"id": "x"}}],
    )
    def test_junk_loads_as_an_empty_record(self, payload: object) -> None:
        """A corrupt store must not stop the integration from loading."""
        restored = stored_state_from_dict(payload)
        assert restored.record.approved is None
        assert restored.smart_charging is True

    def test_an_unknown_reason_does_not_break_the_proposal(self) -> None:
        payload = stored_state_to_dict(
            StoredState(record=fold(PlanRecord(), plan_at(9)))
        )
        payload["proposal_reason"] = "invented_by_a_later_version"

        record = stored_state_from_dict(payload).record
        assert record.proposal is not None
        assert record.proposal_reason is None

    def test_a_reason_without_a_proposal_is_dropped(self) -> None:
        payload = stored_state_to_dict(StoredState())
        payload["proposal_reason"] = PlanSource.MANUAL.value

        assert stored_state_from_dict(payload).record.proposal_reason is None
