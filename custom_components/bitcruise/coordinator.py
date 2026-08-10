"""Reads the user's selected entities and keeps the charge requirement current."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_APPROVAL_POLICY,
    CONF_AVAILABILITY_ENTITY,
    CONF_CAPACITY_ENTITY,
    CONF_CAPACITY_FIXED_KWH,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
    CONF_MATERIAL_CHANGE_MINUTES,
    CONF_NOT_BEFORE,
    CONF_PLUG_ENTITY,
    CONF_PRICE_ENTITY,
    CONF_READY_BY,
    CONF_RESERVE_FLOOR_PCT,
    CONF_SOC_ENTITY,
    CONF_TARGET_ENTITY,
    CONF_TARGET_FIXED_PCT,
    DEFAULT_CHARGING_EFFICIENCY,
    DEFAULT_CHARGING_POWER_KW,
    DEFAULT_MATERIAL_CHANGE_MINUTES,
    DEFAULT_READY_BY,
    DEFAULT_RESERVE_FLOOR_PCT,
    DOMAIN,
)
from .models import (
    ApprovalPolicy,
    ChargePlan,
    ChargeRequirement,
    InvalidPlanningInput,
    PlanningInput,
    PlanStatus,
    to_utc,
)
from .plan_state import (
    PlanInputs,
    PlanRecord,
    StoredState,
    accept,
    clear_rejection,
    price_fingerprint,
    proposal_reason,
    reconcile,
    reject,
)
from .planner import compute_requirement, plan_charging
from .price_sources import PriceData, parse_price_attributes
from .source_normalization import (
    DataFreshness,
    PlugStatus,
    SourceUnavailable,
    normalize_energy_kwh,
    normalize_freshness,
    normalize_percentage,
    normalize_plug_status,
)
from .storage import PlanStore
from .summary import summarize

_LOGGER = logging.getLogger(__name__)

type BitCruiseConfigEntry = ConfigEntry[BitCruiseCoordinator]


@dataclass(frozen=True, slots=True)
class BitCruiseData:
    """Everything the entities need for one evaluation."""

    requirement: ChargeRequirement | None
    plan: ChargePlan | None
    problems: tuple[str, ...]
    plug_status: PlugStatus
    freshness: DataFreshness
    current_soc_pct: float | None
    target_soc_pct: float | None
    usable_capacity_kwh: float | None
    ready_by: datetime | None
    evaluated_at: datetime
    not_before: datetime | None = None
    price_data: PriceData | None = None
    record: PlanRecord = field(default_factory=PlanRecord)
    smart_charging: bool = True
    recalculated: bool = False
    """Whether this evaluation is the one a press of Recalculate produced."""
    approval_policy: ApprovalPolicy = ApprovalPolicy.ASK_ON_CHANGE

    @property
    def is_usable(self) -> bool:
        """Whether a requirement could be computed at all."""
        return self.requirement is not None

    @property
    def effective_plan(self) -> ChargePlan | None:
        """The plan the figures on the dashboard should describe.

        An approved plan outranks a pending proposal, which outranks the raw
        candidate. Without this, accepting a plan would blank the cost and
        end-of-charge estimates until the next replan happened to agree.
        """
        return self.record.approved or self.record.proposal or self.plan

    @property
    def status(self) -> PlanStatus:
        """Lifecycle state, combining the record with whether charging is needed.

        ERROR is deliberately distinct from "no charge needed": the first is a
        configuration or availability problem, the second is a healthy outcome.
        """
        if self.requirement is None:
            return PlanStatus.ERROR
        if not self.smart_charging:
            return PlanStatus.IDLE
        if self.record.proposal is not None:
            return PlanStatus.AWAITING_APPROVAL
        if self.record.approved is not None:
            return PlanStatus.APPROVED
        if not self.requirement.is_charge_needed:
            return PlanStatus.IDLE
        return PlanStatus.NEEDS_CHARGE

    @property
    def currency(self) -> str | None:
        """Currency the price source quotes, if it states one."""
        return self.price_data.currency if self.price_data else None

    @property
    def summary(self) -> str:
        """One sentence describing the current state.

        Assembled here rather than in the sensor so a notification can send the
        same sentence the dashboard shows, without composing its own.
        """
        return summarize(
            status=self.status,
            now=self.evaluated_at,
            plan=self.effective_plan,
            requirement=self.requirement,
            problems=self.problems,
            currency=self.currency,
            smart_charging=self.smart_charging,
            is_replacement=self.record.is_replacement,
            proposal_reason=self.record.proposal_reason,
            ready_by=self.ready_by,
            current_soc_pct=self.current_soc_pct,
            target_soc_pct=self.target_soc_pct,
            recalculated=self.recalculated,
        )

    @property
    def price_interval_count(self) -> int:
        """How many intervals were parsed out of the price entity."""
        return len(self.price_data.intervals) if self.price_data else 0

    @property
    def cheapest_price_in_horizon(self) -> Decimal | None:
        """Lowest price anywhere in the known curve, deadline ignored."""
        if self.price_data is None or not self.price_data.intervals:
            return None
        return min(interval.price_per_kwh for interval in self.price_data.intervals)


def parse_time_option(value: str | None) -> time | None:
    """Parse an ``HH:MM:SS`` option into a time, tolerating a missing value."""
    if not value:
        return None
    parsed = dt_util.parse_time(value)
    return parsed


def next_occurrence(now: datetime, target: time) -> datetime:
    """Next time the local clock next shows ``target``.

    Wall-clock arithmetic is correct here and only here: "ready by 07:00" means the
    07:00 a human reads off a clock, so a DST day legitimately becomes 23 or 25
    hours long. Everywhere else in this project, durations go through ``to_utc``.
    """
    local_now = dt_util.as_local(now)
    candidate = local_now.replace(
        hour=target.hour,
        minute=target.minute,
        second=target.second,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return candidate


def next_evaluation_boundary(
    now: datetime, moments: Iterable[datetime | None]
) -> datetime | None:
    """Earliest of ``moments`` still in the future, in UTC.

    Comparison goes through ``to_utc`` because two of these instants can share a
    wall-clock time on a DST fall-back day.
    """
    reference = to_utc(now)
    future = [
        utc
        for moment in moments
        if moment is not None and (utc := to_utc(moment)) > reference
    ]
    return min(future) if future else None


class BitCruiseCoordinator(DataUpdateCoordinator[BitCruiseData]):
    """Recomputes the charge requirement whenever a source entity changes.

    Vendor integrations already push their own updates, so nothing here polls
    them. Time passing is the one thing that is not an entity event, and it is
    handled by scheduling a single wake-up at the next instant that can change
    the answer rather than by ticking.
    """

    config_entry: BitCruiseConfigEntry

    def __init__(self, hass: HomeAssistant, entry: BitCruiseConfigEntry) -> None:
        """Set up the coordinator for a config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=None,
        )
        self._unsub_boundary: CALLBACK_TYPE | None = None
        self._store = PlanStore(hass, entry.entry_id)
        self._record = PlanRecord()
        self._smart_charging = True
        self._approval_policy = ApprovalPolicy.ASK_ON_CHANGE
        self._previous_inputs: PlanInputs | None = None
        self._manual_request = False

    @property
    def options(self) -> dict:
        """Merged entry data and options, with options winning."""
        return {**self.config_entry.data, **self.config_entry.options}

    @property
    def approval_policy(self) -> ApprovalPolicy:
        """How much the user wants to be asked, as the select entity has it."""
        return self._approval_policy

    def _initial_approval_policy(self, stored: ApprovalPolicy | None) -> ApprovalPolicy:
        """Resolve the policy for a freshly loaded entry.

        The setting used to live in the config entry options, before it became a
        control worth changing from a dashboard. An installation that predates
        the select entity keeps the policy it was configured with.
        """
        if stored is not None:
            return stored
        try:
            return ApprovalPolicy(self.options.get(CONF_APPROVAL_POLICY))
        except ValueError:
            return ApprovalPolicy.ASK_ON_CHANGE

    @property
    def material_change(self) -> timedelta:
        """How far a window must move before the user is asked about it."""
        minutes = self.options.get(
            CONF_MATERIAL_CHANGE_MINUTES, DEFAULT_MATERIAL_CHANGE_MINUTES
        )
        return timedelta(minutes=float(minutes))

    async def async_load_stored_state(self) -> None:
        """Restore the approval record before the first evaluation.

        Ordering matters: evaluating first would reconcile a fresh candidate
        against an empty record and, under ask_on_change, approve a plan that
        replaced the one the user had already answered on.
        """
        stored = await self._store.async_load()
        self._record = stored.record
        self._smart_charging = stored.smart_charging
        self._approval_policy = self._initial_approval_policy(stored.approval_policy)

    async def _async_save(self) -> None:
        """Write the record out."""
        await self._store.async_save(
            StoredState(
                record=self._record,
                smart_charging=self._smart_charging,
                approval_policy=self._approval_policy,
            )
        )

    async def async_remove_stored_state(self) -> None:
        """Forget everything, when the config entry is deleted."""
        await self._store.async_remove()

    async def async_accept(self) -> None:
        """Approve the pending proposal."""
        self._record = accept(self._record)
        await self._async_save()
        await self.async_refresh()

    async def async_reject(self) -> None:
        """Discard the pending proposal, leaving any approved plan alone."""
        self._record = reject(self._record)
        await self._async_save()
        await self.async_refresh()

    async def async_recalculate(self) -> None:
        """Reconsider from scratch, including a window already turned down."""
        self._record = clear_rejection(self._record)
        self._manual_request = True
        await self.async_refresh()
        # Saved unconditionally: the refresh only persists when reconcile
        # changed something, and a recalculation that reaches the same plan
        # changes nothing except the rejection just cleared.
        await self._async_save()

    async def async_set_approval_policy(self, policy: ApprovalPolicy) -> None:
        """Change how much the user wants to be asked, and act on it now.

        Switching to a laxer policy resolves whatever is currently pending
        rather than leaving a question on screen that nothing will ever answer.
        """
        if self._approval_policy is policy:
            return
        self._approval_policy = policy
        await self.async_refresh()
        await self._async_save()

    async def async_set_smart_charging(self, enabled: bool) -> None:
        """Turn planning on or off.

        Off means BitCruise decides nothing: the deficit figures keep reporting,
        but nothing is proposed and no plan is held approved.
        """
        if self._smart_charging == enabled:
            return
        self._smart_charging = enabled
        await self.async_refresh()
        await self._async_save()

    def tracked_entities(self) -> list[str]:
        """Entity ids whose changes should trigger a recomputation."""
        keys = (
            CONF_SOC_ENTITY,
            CONF_TARGET_ENTITY,
            CONF_CAPACITY_ENTITY,
            CONF_PLUG_ENTITY,
            CONF_AVAILABILITY_ENTITY,
            CONF_PRICE_ENTITY,
        )
        options = self.options
        return [value for key in keys if (value := options.get(key))]

    async def async_setup_listeners(self) -> None:
        """Subscribe to state changes on every selected entity.

        Attribute-only changes raise a state change event too, which is what
        carries a refreshed price curve and the ``tomorrow_valid`` flip.
        """
        self.config_entry.async_on_unload(self._cancel_boundary)

        entities = self.tracked_entities()
        if not entities:
            return

        @callback
        def _handle(event: Event[EventStateChangedData]) -> None:
            self.config_entry.async_create_task(
                self.hass, self.async_refresh(), eager_start=True
            )

        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, entities, _handle)
        )

    @callback
    def _cancel_boundary(self) -> None:
        """Drop any pending wake-up."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None

    def _boundary_moments(self, data: BitCruiseData) -> list[datetime | None]:
        """Instants after which the current answer may no longer hold."""
        moments: list[datetime | None] = [data.ready_by, data.not_before]
        if data.price_data is not None:
            for interval in data.price_data.intervals:
                moments.append(interval.start)
                moments.append(interval.end)
        if data.plan is not None:
            moments.append(data.plan.start)
            moments.append(data.plan.end)
        return moments

    @callback
    def _schedule_boundary(self, data: BitCruiseData | None) -> None:
        """Wake up once, at the next instant that can change the answer.

        Source entities push their own changes, but the clock does not. Crossing
        ready-by moves the deadline to the next day, and a price interval ending
        drops it out of the horizon; without this the plan would only refresh
        when some unrelated entity happened to update.
        """
        self._cancel_boundary()
        if data is None:
            return
        moment = next_evaluation_boundary(
            dt_util.utcnow(), self._boundary_moments(data)
        )
        if moment is None:
            return
        self._unsub_boundary = async_track_point_in_time(
            self.hass, self._handle_boundary, moment
        )

    async def _handle_boundary(self, _now: datetime) -> None:
        """Recompute because the clock passed something that matters."""
        self._unsub_boundary = None
        await self.async_refresh()

    async def _async_update_data(self) -> BitCruiseData:
        """Recompute from current entity states, persisting any approval change."""
        data = self._evaluate()
        self._schedule_boundary(data)
        if data.record != self._record:
            self._record = data.record
            await self._async_save()
        return data

    def _read_percentage(self, entity_id: str, problems: list[str]) -> float | None:
        """Read a percentage from an entity, recording any problem."""
        state = self.hass.states.get(entity_id)
        try:
            return normalize_percentage(entity_id, state.state if state else None)
        except SourceUnavailable as err:
            problems.append(str(err))
            return None

    def _read_target(self, problems: list[str]) -> float | None:
        """Resolve the charge target, preferring the selected entity.

        Never inferred from the current state of charge; if neither source is
        usable the requirement is left uncomputed.
        """
        options = self.options
        if entity_id := options.get(CONF_TARGET_ENTITY):
            if (value := self._read_percentage(entity_id, problems)) is not None:
                return value
            return None
        fixed = options.get(CONF_TARGET_FIXED_PCT)
        if fixed is None:
            problems.append("no charge target configured")
            return None
        return float(fixed)

    def _read_capacity(self, problems: list[str]) -> float | None:
        """Resolve usable battery capacity in kWh."""
        options = self.options
        if entity_id := options.get(CONF_CAPACITY_ENTITY):
            state = self.hass.states.get(entity_id)
            try:
                return normalize_energy_kwh(
                    entity_id,
                    state.state if state else None,
                    state.attributes.get("unit_of_measurement") if state else None,
                )
            except SourceUnavailable as err:
                problems.append(str(err))
                return None
        fixed = options.get(CONF_CAPACITY_FIXED_KWH)
        if fixed is None:
            problems.append("no battery capacity configured")
            return None
        return float(fixed)

    def _read_state(self, key: str) -> str | None:
        """Raw state of an optional configured entity."""
        entity_id = self.options.get(key)
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.state if state else None

    def _evaluate(self) -> BitCruiseData:
        """Read every source and compute the requirement, or explain why not."""
        options = self.options
        problems: list[str] = []

        soc = self._read_percentage(options[CONF_SOC_ENTITY], problems)
        target = self._read_target(problems)
        capacity = self._read_capacity(problems)

        plug = normalize_plug_status(self._read_state(CONF_PLUG_ENTITY))
        freshness = normalize_freshness(self._read_state(CONF_AVAILABILITY_ENTITY))
        if freshness is DataFreshness.STALE:
            problems.append("vehicle readings may be stale")

        ready_by_time = parse_time_option(options.get(CONF_READY_BY, DEFAULT_READY_BY))
        now = dt_util.now()
        ready_by = next_occurrence(now, ready_by_time) if ready_by_time else None
        not_before_time = parse_time_option(options.get(CONF_NOT_BEFORE))
        not_before = next_occurrence(now, not_before_time) if not_before_time else None

        price_data = self._read_prices(problems)

        requirement: ChargeRequirement | None = None
        plan: ChargePlan | None = None
        if soc is not None and target is not None and capacity is not None:
            floor = float(
                options.get(CONF_RESERVE_FLOOR_PCT, DEFAULT_RESERVE_FLOOR_PCT)
            )
            if floor > target:
                # Clamp rather than refuse. The floor is an optional extra that
                # does not affect how much energy the target needs, so letting a
                # bad floor blank the deficit figures would withhold correct
                # information over an unrelated setting. The problem is still
                # surfaced; it is not silently reordered. See DESIGN.md 5.
                problems.append(
                    f"reserve floor ({floor:g}%) exceeds charge target "
                    f"({target:g}%) and has been ignored"
                )
                floor = 0.0
            try:
                planning_input = PlanningInput(
                    now=now,
                    current_soc_pct=soc,
                    target_soc_pct=target,
                    usable_capacity_kwh=capacity,
                    charging_power_kw=float(
                        options.get(CONF_CHARGING_POWER_KW, DEFAULT_CHARGING_POWER_KW)
                    ),
                    ready_by=ready_by or now + timedelta(days=1),
                    charging_efficiency=float(
                        options.get(
                            CONF_CHARGING_EFFICIENCY, DEFAULT_CHARGING_EFFICIENCY
                        )
                    )
                    / 100.0,
                    reserve_floor_pct=floor,
                    not_before=not_before,
                    price_intervals=price_data.intervals if price_data else (),
                )
                requirement = compute_requirement(planning_input)
                if price_data is not None and price_data.is_usable:
                    plan = plan_charging(planning_input)
            except InvalidPlanningInput as err:
                problems.append(str(err))

        # Captured before _reconcile consumes it, so the summary can say what a
        # press of Recalculate concluded.
        recalculated = self._manual_request

        record = self._reconcile(
            plan,
            now=now,
            requirement=requirement,
            price_data=price_data,
            inputs=PlanInputs(
                current_soc_pct=soc,
                target_soc_pct=target,
                usable_capacity_kwh=capacity,
                charging_power_kw=float(
                    options.get(CONF_CHARGING_POWER_KW, DEFAULT_CHARGING_POWER_KW)
                ),
                ready_by=ready_by,
                prices=price_fingerprint(price_data.intervals if price_data else ()),
            ),
        )

        return BitCruiseData(
            requirement=requirement,
            plan=plan,
            problems=tuple(problems),
            plug_status=plug,
            freshness=freshness,
            current_soc_pct=soc,
            target_soc_pct=target,
            usable_capacity_kwh=capacity,
            ready_by=ready_by,
            evaluated_at=now,
            not_before=not_before,
            price_data=price_data,
            record=record,
            smart_charging=self._smart_charging,
            recalculated=recalculated,
            approval_policy=self._approval_policy,
        )

    def _reconcile(
        self,
        candidate: ChargePlan | None,
        *,
        now: datetime,
        requirement: ChargeRequirement | None,
        price_data: PriceData | None,
        inputs: PlanInputs,
    ) -> PlanRecord:
        """Fold this evaluation into the stored approval record.

        The evaluation counts as usable only when the requirement was computed
        *and* the configured price source produced something. A price entity
        that briefly goes unavailable must not look like "no charging needed"
        and take a pending proposal down with it.
        """
        prices_configured = bool(self.options.get(CONF_PRICE_ENTITY))
        usable = requirement is not None and (
            not prices_configured or (price_data is not None and price_data.is_usable)
        )
        result = reconcile(
            self._record,
            candidate,
            now=now,
            policy=self.approval_policy,
            material_change=self.material_change,
            reason=proposal_reason(
                self._previous_inputs, inputs, manual=self._manual_request
            ),
            smart_charging=self._smart_charging,
            inputs_usable=usable,
        )
        self._previous_inputs = inputs
        self._manual_request = False
        return result

    def _read_prices(self, problems: list[str]) -> PriceData | None:
        """Parse the selected price entity, if one is configured."""
        entity_id = self.options.get(CONF_PRICE_ENTITY)
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None:
            # Distinct from the entity being unavailable, and it needs a
            # different fix: a missing entity id means the integration that
            # provides it is not loaded, or the wrong entity was selected.
            problems.append(f"{entity_id}: entity not found")
            return None
        if state.state in ("unknown", "unavailable"):
            problems.append(f"{entity_id}: state is {state.state}")
            return None

        data = parse_price_attributes(state.attributes, source=entity_id)
        problems.extend(f"{entity_id}: {problem}" for problem in data.problems)
        return data
