"""Tests for the price-source adapter.

The primary fixture is a sanitized snapshot of a real Energi Data Service sensor,
so these assert against attribute shapes that actually occur rather than ones the
implementation assumes.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from custom_components.bitcruise.models import (
    PlanPriceQuality,
    PriceQuality,
    to_utc,
)
from custom_components.bitcruise.price_sources import parse_price_attributes

from .builders import CPH

FIXTURE = Path(__file__).parent / "fixtures" / "energidataservice.json"


@pytest.fixture
def eds() -> dict[str, Any]:
    """Attributes from the captured Energi Data Service sensor."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["attributes"]


def hour(value: str) -> datetime:
    """Parse a fixture timestamp."""
    return datetime.fromisoformat(value)


class TestRealEnergiDataService:
    """Parsing the captured snapshot."""

    def test_parses_all_three_curves(self, eds: dict[str, Any]) -> None:
        """24 actual today, 24 actual tomorrow, 24 forecast, none overlapping."""
        data = parse_price_attributes(eds)
        assert len(data.intervals) == 72
        assert data.quality is PlanPriceQuality.MIXED

    def test_intervals_are_sorted_and_contiguous(self, eds: dict[str, Any]) -> None:
        data = parse_price_attributes(eds)
        for previous, current in zip(data.intervals, data.intervals[1:], strict=False):
            assert to_utc(current.start) == to_utc(previous.end)

    def test_first_and_last_moments(self, eds: dict[str, Any]) -> None:
        data = parse_price_attributes(eds)
        assert data.intervals[0].start == hour("2026-08-09T00:00:00+02:00")
        assert data.intervals[-1].end == hour("2026-08-12T00:00:00+02:00")

    def test_prices_are_exact_decimals(self, eds: dict[str, Any]) -> None:
        """Prices must not pick up float noise on the way in."""
        data = parse_price_attributes(eds)
        assert data.intervals[0].price_per_kwh == Decimal("1.759")

    def test_quality_is_tracked_per_interval(self, eds: dict[str, Any]) -> None:
        data = parse_price_attributes(eds)
        actual = [i for i in data.intervals if i.quality is PriceQuality.ACTUAL]
        forecast = [i for i in data.intervals if i.quality is PriceQuality.FORECAST]
        assert len(actual) == 48
        assert len(forecast) == 24

    def test_currency_is_carried_through(self, eds: dict[str, Any]) -> None:
        """Cost must not be labelled in an assumed currency."""
        assert parse_price_attributes(eds).currency == "DKK"

    def test_no_problems_on_good_data(self, eds: dict[str, Any]) -> None:
        assert parse_price_attributes(eds).problems == ()

    def test_hourly_intervals(self, eds: dict[str, Any]) -> None:
        data = parse_price_attributes(eds)
        assert all(i.duration_hours == 1.0 for i in data.intervals)

    def test_tariffs_are_not_added(self, eds: dict[str, Any]) -> None:
        """EDS already includes tariffs; adding them again inflates cost ~40%."""
        data = parse_price_attributes(eds)
        seventeen = next(
            i for i in data.intervals if i.start == hour("2026-08-09T17:00:00+02:00")
        )
        assert seventeen.price_per_kwh == Decimal("1.486")


class TestUnits:
    """A price is only meaningful alongside its unit."""

    def _one_hour(self, unit: str | None, price: str, **extra: Any) -> Any:
        attributes: dict[str, Any] = {
            "raw_today": [{"hour": "2026-08-09T00:00:00+02:00", "price": price}],
            **extra,
        }
        if unit is not None:
            attributes["unit"] = unit
        return parse_price_attributes(attributes)

    def test_kwh_passes_through(self) -> None:
        assert self._one_hour("kWh", "1.5").intervals[0].price_per_kwh == Decimal("1.5")

    def test_mwh_is_scaled_down(self) -> None:
        """A per-MWh price is 1000x too large if taken at face value."""
        result = self._one_hour("MWh", "1500")
        assert result.intervals[0].price_per_kwh == Decimal("1.5")

    def test_wh_is_scaled_up(self) -> None:
        assert self._one_hour("Wh", "0.0015").intervals[0].price_per_kwh == Decimal(
            "1.5"
        )

    def test_use_cent_divides_by_one_hundred(self) -> None:
        result = self._one_hour("kWh", "150", use_cent=True)
        assert result.intervals[0].price_per_kwh == Decimal("1.5")

    def test_missing_unit_assumes_kwh(self) -> None:
        """Most price sensors omit it; kWh is the near-universal convention."""
        assert self._one_hour(None, "1.5").intervals[0].price_per_kwh == Decimal("1.5")

    def test_unsupported_unit_is_refused(self) -> None:
        result = self._one_hour("J", "1.5")
        assert not result.is_usable
        assert "unsupported price unit" in result.problems[0]


class TestActualSupersedesForecast:
    """Settled prices must win over predictions for the same hour."""

    ATTRS = {
        "unit": "kWh",
        "raw_today": [{"hour": "2026-08-09T00:00:00+02:00", "price": 1.0}],
        "forecast": [
            {"hour": "2026-08-09T00:00:00+02:00", "price": 9.0},
            {"hour": "2026-08-09T01:00:00+02:00", "price": 8.0},
        ],
    }

    def test_overlapping_hour_keeps_the_actual_price(self) -> None:
        data = parse_price_attributes(self.ATTRS)
        first = data.intervals[0]
        assert first.price_per_kwh == Decimal("1.0")
        assert first.quality is PriceQuality.ACTUAL

    def test_non_overlapping_forecast_is_kept(self) -> None:
        data = parse_price_attributes(self.ATTRS)
        assert len(data.intervals) == 2
        assert data.intervals[1].quality is PriceQuality.FORECAST

    def test_quality_is_mixed(self) -> None:
        assert parse_price_attributes(self.ATTRS).quality is PlanPriceQuality.MIXED


class TestAttributeVariants:
    """Different integrations name things differently."""

    def test_start_end_value_keys(self) -> None:
        """Nordpool-style entries use start/end/value rather than hour/price."""
        data = parse_price_attributes(
            {
                "unit": "kWh",
                "raw_today": [
                    {
                        "start": "2026-08-09T00:00:00+02:00",
                        "end": "2026-08-09T01:00:00+02:00",
                        "value": 1.25,
                    }
                ],
            }
        )
        assert data.intervals[0].price_per_kwh == Decimal("1.25")
        assert data.intervals[0].duration_hours == 1.0

    def test_quarter_hourly_final_interval_is_not_stretched(self) -> None:
        """The last entry has no successor; it must not become an hour long."""
        base = datetime(2026, 8, 9, tzinfo=CPH)
        entries = [
            {
                "hour": (base + timedelta(minutes=15 * i)).isoformat(),
                "price": 1.0,
            }
            for i in range(4)
        ]
        data = parse_price_attributes({"unit": "kWh", "raw_today": entries})
        assert all(i.duration_hours == 0.25 for i in data.intervals)


class TestBadData:
    """Malformed input must be refused, never guessed at."""

    def test_no_attributes(self) -> None:
        result = parse_price_attributes({})
        assert not result.is_usable
        assert "no price data found" in result.problems[0]

    def test_naive_timestamps_are_skipped(self) -> None:
        """Without an offset a price cannot be placed on the timeline."""
        result = parse_price_attributes(
            {"unit": "kWh", "raw_today": [{"hour": "2026-08-09T00:00:00", "price": 1}]}
        )
        assert not result.is_usable

    def test_unparseable_entries_are_skipped(self) -> None:
        result = parse_price_attributes(
            {
                "unit": "kWh",
                "raw_today": [
                    {"hour": "not a time", "price": 1},
                    {"hour": "2026-08-09T00:00:00+02:00", "price": "oops"},
                    {"hour": "2026-08-09T01:00:00+02:00", "price": 2.0},
                ],
            }
        )
        assert len(result.intervals) == 1
        assert result.intervals[0].price_per_kwh == Decimal("2.0")

    def test_empty_tomorrow_is_not_an_error(self) -> None:
        """raw_tomorrow is empty until the day-ahead auction publishes."""
        result = parse_price_attributes(
            {
                "unit": "kWh",
                "raw_today": [{"hour": "2026-08-09T00:00:00+02:00", "price": 1.0}],
                "raw_tomorrow": [],
            }
        )
        assert result.is_usable
        assert result.problems == ()

    def test_forecast_only_is_usable(self) -> None:
        """Before tomorrow's prices exist, a forecast-based plan is still useful."""
        result = parse_price_attributes(
            {
                "unit": "kWh",
                "raw_tomorrow": [],
                "forecast": [{"hour": "2026-08-09T00:00:00+02:00", "price": 1.0}],
            }
        )
        assert result.quality is PlanPriceQuality.FORECAST

    def test_non_list_attribute_is_ignored(self) -> None:
        result = parse_price_attributes({"unit": "kWh", "raw_today": "nonsense"})
        assert not result.is_usable
