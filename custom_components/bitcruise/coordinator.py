"""Reads the user's selected entities and keeps the charge requirement current."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
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
)
from .models import ChargeRequirement, InvalidPlanningInput, PlanningInput
from .planner import compute_requirement
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
    problems: tuple[str, ...]
    plug_status: PlugStatus
    freshness: DataFreshness
    current_soc_pct: float | None
    target_soc_pct: float | None
    usable_capacity_kwh: float | None
    ready_by: datetime | None

    @property
    def is_usable(self) -> bool:
        """Whether a requirement could be computed at all."""
        return self.requirement is not None


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


class BitCruiseCoordinator(DataUpdateCoordinator[BitCruiseData]):
    """Recomputes the charge requirement whenever a source entity changes.

    There is no polling. Vendor integrations already push their own updates, and
    polling them again would only add latency and log noise.
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
        )
        options = self.options
        return [value for key in keys if (value := options.get(key))]

    async def async_setup_listeners(self) -> None:
        """Subscribe to state changes on every selected entity."""
        entities = self.tracked_entities()
        if not entities:
            return

        @callback
        def _handle(event: Event[EventStateChangedData]) -> None:
            self.async_set_updated_data(self._evaluate())

        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, entities, _handle)
        )

    async def _async_update_data(self) -> BitCruiseData:
        """Recompute from current entity states."""
        return self._evaluate()

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

        requirement: ChargeRequirement | None = None
        if soc is not None and target is not None and capacity is not None:
            not_before_time = parse_time_option(options.get(CONF_NOT_BEFORE))
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
                    not_before=(
                        next_occurrence(now, not_before_time)
                        if not_before_time
                        else None
                    ),
                )
                requirement = compute_requirement(planning_input)
            except InvalidPlanningInput as err:
                problems.append(str(err))

        return BitCruiseData(
            requirement=requirement,
            problems=tuple(problems),
            plug_status=plug,
            freshness=freshness,
            current_soc_pct=soc,
            target_soc_pct=target,
            usable_capacity_kwh=capacity,
            ready_by=ready_by,
        )
