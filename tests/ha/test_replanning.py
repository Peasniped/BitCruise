"""When a recomputation happens: the clock, and bursts of source updates.

Source entities push their own changes, so those are covered by ordinary state
change tracking. The cases worth testing here are the two that are not events:
time passing, and several entities changing at once.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from custom_components.bitcruise.const import (
    CONF_CAPACITY_ENTITY,
    CONF_CHARGING_EFFICIENCY,
    CONF_CHARGING_POWER_KW,
    CONF_PRICE_ENTITY,
    CONF_READY_BY,
    CONF_RESERVE_FLOOR_PCT,
    CONF_SOC_ENTITY,
    CONF_TARGET_ENTITY,
    DOMAIN,
    REPLAN_DEBOUNCE_SECONDS,
)
from custom_components.bitcruise.coordinator import next_evaluation_boundary
from custom_components.bitcruise.models import to_utc
from freezegun import freeze_time
from homeassistant.core import HomeAssistant, callback
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

CPH = ZoneInfo("Europe/Copenhagen")
FIXTURE = Path(__file__).parent.parent / "fixtures" / "energidataservice.json"

SOC = "sensor.car_battery"
TARGET = "sensor.car_target"
CAPACITY = "sensor.car_capacity"
PRICE = "sensor.electricity_price"

SOURCES: dict[str, Any] = {
    CONF_SOC_ENTITY: SOC,
    CONF_TARGET_ENTITY: TARGET,
    CONF_CAPACITY_ENTITY: CAPACITY,
    CONF_PRICE_ENTITY: PRICE,
}
SETTINGS: dict[str, Any] = {
    CONF_CHARGING_POWER_KW: 11.0,
    CONF_CHARGING_EFFICIENCY: 90,
    CONF_RESERVE_FLOOR_PCT: 0,
    CONF_READY_BY: "07:00:00",
}


def _price_attributes() -> dict[str, Any]:
    """Attributes from the captured Energi Data Service sensor."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["attributes"]


async def _setup(
    hass: HomeAssistant, price_attributes: dict[str, Any] | None = None
) -> MockConfigEntry:
    """Set up an entry with the real price fixture."""
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    hass.states.async_set(SOC, "53", {"unit_of_measurement": "%"})
    hass.states.async_set(TARGET, "90", {"unit_of_measurement": "%"})
    hass.states.async_set(CAPACITY, "81.608", {"unit_of_measurement": "kWh"})
    hass.states.async_set(PRICE, "1.759", price_attributes or _price_attributes())

    entry = MockConfigEntry(
        domain=DOMAIN, data=SOURCES, options=SETTINGS, title="BitCruise"
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _ready_by(hass: HomeAssistant) -> datetime:
    """Ready-by as the sensor currently reports it, in local time."""
    state = hass.states.get("sensor.bitcruise_ready_by")
    return datetime.fromisoformat(state.state).astimezone(CPH)


async def test_ready_by_rolls_over_when_the_clock_passes_it(
    hass: HomeAssistant,
) -> None:
    """Crossing the deadline moves it to the next day with no entity change.

    Nothing about the car or the prices changes here. Before the scheduled
    wake-up existed, the deadline only advanced when some unrelated entity
    happened to update, which made the behaviour undefined rather than wrong.
    """
    with freeze_time(datetime(2026, 8, 10, 6, 0, tzinfo=CPH)) as frozen:
        await _setup(hass)
        assert _ready_by(hass) == datetime(2026, 8, 10, 7, 0, tzinfo=CPH)

        passed = datetime(2026, 8, 10, 7, 0, 30, tzinfo=CPH)
        frozen.move_to(passed)
        async_fire_time_changed(hass, passed)
        await hass.async_block_till_done()

        assert _ready_by(hass) == datetime(2026, 8, 11, 7, 0, tzinfo=CPH)


class TestNextEvaluationBoundary:
    """Choosing the single instant worth waking up for."""

    NOW = datetime(2026, 8, 9, 18, 30, tzinfo=CPH)

    def test_earliest_future_moment_wins(self) -> None:
        chosen = next_evaluation_boundary(
            self.NOW,
            [
                datetime(2026, 8, 10, 7, 0, tzinfo=CPH),
                datetime(2026, 8, 9, 19, 0, tzinfo=CPH),
                datetime(2026, 8, 9, 20, 0, tzinfo=CPH),
            ],
        )
        assert chosen == datetime(2026, 8, 9, 19, 0, tzinfo=CPH)

    def test_past_moments_are_ignored(self) -> None:
        """A boundary already behind us would fire immediately, and forever."""
        chosen = next_evaluation_boundary(
            self.NOW,
            [
                datetime(2026, 8, 9, 17, 0, tzinfo=CPH),
                datetime(2026, 8, 9, 18, 30, tzinfo=CPH),
                datetime(2026, 8, 9, 21, 0, tzinfo=CPH),
            ],
        )
        assert chosen == datetime(2026, 8, 9, 21, 0, tzinfo=CPH)

    def test_nothing_ahead_schedules_nothing(self) -> None:
        assert next_evaluation_boundary(self.NOW, [None]) is None
        assert next_evaluation_boundary(self.NOW, []) is None

    def test_fall_back_hour_is_compared_as_an_instant(self) -> None:
        """The repeated 02:00 is two instants; wall-clock order would tie them.

        On 25 October 2026 Copenhagen goes 02:00 CEST then 02:00 CET. The second
        is an hour later in real time, so the first must be chosen.
        """
        first = datetime(2026, 10, 25, 2, 0, tzinfo=CPH, fold=0)
        second = datetime(2026, 10, 25, 2, 0, tzinfo=CPH, fold=1)
        now = datetime(2026, 10, 25, 1, 30, tzinfo=CPH)

        assert next_evaluation_boundary(now, [second, first]) == to_utc(first)
        assert to_utc(first) != to_utc(second)


async def test_a_burst_of_source_changes_recomputes_once(
    hass: HomeAssistant,
) -> None:
    """A waking car updates several entities at once; that is one replan.

    The first change still applies immediately, so the dashboard does not lag
    behind the car by the cooldown.
    """
    with freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH)):
        entry = await _setup(hass)
        updates = 0

        @callback
        def _count() -> None:
            nonlocal updates
            updates += 1

        entry.runtime_data.async_add_listener(_count)

        for soc in ("54", "55", "56", "57", "58"):
            hass.states.async_set(SOC, soc, {"unit_of_measurement": "%"})
        await hass.async_block_till_done()

        assert updates == 1


async def test_the_cooldown_releases_a_trailing_recomputation(
    hass: HomeAssistant,
) -> None:
    """Changes coalesced during the cooldown are not simply dropped."""
    with freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH)) as frozen:
        entry = await _setup(hass)

        for soc in ("54", "55"):
            hass.states.async_set(SOC, soc, {"unit_of_measurement": "%"})
        await hass.async_block_till_done()

        # The immediate recomputation saw 54; 55 arrived inside the cooldown.
        assert entry.runtime_data.data.current_soc_pct == 54.0

        later = datetime(2026, 8, 9, 18, 0, tzinfo=CPH) + timedelta(
            seconds=REPLAN_DEBOUNCE_SECONDS + 1
        )
        frozen.move_to(later)
        async_fire_time_changed(hass, later)
        await hass.async_block_till_done()

        assert entry.runtime_data.data.current_soc_pct == 55.0


async def test_status_reports_what_the_price_adapter_parsed(
    hass: HomeAssistant,
) -> None:
    """Correctness of the parse must be confirmable by reading a sensor."""
    with freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH)):
        await _setup(hass)

        attributes = hass.states.get("sensor.bitcruise_plan_status").attributes
        assert attributes["price_source"] == PRICE
        assert attributes["price_intervals"] == 72
        assert attributes["price_horizon_quality"] == "mixed"
        assert attributes["price_tomorrow_valid"] is True


async def test_unpublished_tomorrow_prices_are_not_planned_on(
    hass: HomeAssistant,
) -> None:
    """Stale entries left in raw_tomorrow are dropped, and the drop is visible."""
    attributes = _price_attributes()
    attributes["tomorrow_valid"] = False

    with freeze_time(datetime(2026, 8, 9, 18, 0, tzinfo=CPH)):
        await _setup(hass, price_attributes=attributes)

        status = hass.states.get("sensor.bitcruise_plan_status").attributes
        assert status["price_tomorrow_valid"] is False
        assert status["price_intervals"] == 48
        assert any("tomorrow" in problem for problem in status["problems"])
