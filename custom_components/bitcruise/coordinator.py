"""Reads the user's selected entities and keeps the charge requirement current."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
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
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AVAILABILITY_ENTITY,
    CONF_CAPACITY_ENTITY,
    CONF_CAPACITY_FIXED_KWH,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
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
    DEFAULT_READY_BY,
    DEFAULT_RESERVE_FLOOR_PCT,
    DOMAIN,
    REPLAN_DEBOUNCE_SECONDS,
)
from .models import (
    ChargePlan,
    ChargeRequirement,
    InvalidPlanningInput,
    PlanningInput,
    to_utc,
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
    not_before: datetime | None = None
    price_data: PriceData | None = None

    @property
    def is_usable(self) -> bool:
        """Whether a requirement could be computed at all."""
        return self.requirement is not None

    @property
    def currency(self) -> str | None:
        """Currency the price source quotes, if it states one."""
        return self.price_data.currency if self.price_data else None

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
            request_refresh_debouncer=Debouncer(
                hass,
                _LOGGER,
                cooldown=REPLAN_DEBOUNCE_SECONDS,
                immediate=True,
            ),
        )
        self._unsub_boundary: CALLBACK_TYPE | None = None

    @property
    def options(self) -> dict:
        """Merged entry data and options, with options winning."""
        return {**self.config_entry.data, **self.config_entry.options}

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

        async def _handle(event: Event[EventStateChangedData]) -> None:
            await self.async_request_refresh()

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
        await self.async_request_refresh()

    async def _async_update_data(self) -> BitCruiseData:
        """Recompute from current entity states."""
        data = self._evaluate()
        self._schedule_boundary(data)
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
            not_before=not_before,
            price_data=price_data,
        )

    def _read_prices(self, problems: list[str]) -> PriceData | None:
        """Parse the selected price entity, if one is configured."""
        entity_id = self.options.get(CONF_PRICE_ENTITY)
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            problems.append(f"{entity_id}: price entity unavailable")
            return None

        data = parse_price_attributes(state.attributes, source=entity_id)
        problems.extend(f"{entity_id}: {problem}" for problem in data.problems)
        return data
