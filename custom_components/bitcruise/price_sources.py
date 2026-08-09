"""Turn a Home Assistant price entity into normalized price intervals.

Pure Python: the caller passes in the entity's state attributes as a plain
mapping, so this whole module is testable without Home Assistant.

The planner only ever sees ``PriceInterval`` objects. Everything vendor-specific
about attribute names, units and forecast handling is confined to this file, per
DESIGN.md section 12.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import (
    PlanPriceQuality,
    PriceInterval,
    PriceQuality,
    to_utc,
)

# Attribute names holding a forward price curve, in the order they are tried.
# Actual prices are listed before forecasts so that a duplicated interval keeps
# the settled value.
_ACTUAL_KEYS: tuple[str, ...] = ("raw_today", "raw_tomorrow")
_FORECAST_KEYS: tuple[str, ...] = ("forecast",)

# Attributes whose contents are only settled once ``tomorrow_valid`` says so.
_TOMORROW_KEYS: frozenset[str] = frozenset({"raw_tomorrow"})

# Per-entry key names. Energi Data Service uses hour/price; Nordpool-style
# sensors use start/end/value. Both are accepted rather than guessed at.
_START_KEYS: tuple[str, ...] = ("hour", "start", "time", "from", "datetime")
_END_KEYS: tuple[str, ...] = ("end", "to")
_PRICE_KEYS: tuple[str, ...] = ("price", "value", "cost")

# Multiplier converting a price quoted per unit into a price per kWh.
_UNIT_TO_PER_KWH: dict[str, Decimal] = {
    "kWh": Decimal(1),
    "MWh": Decimal(1) / Decimal(1000),
    "Wh": Decimal(1000),
}

_DEFAULT_STEP = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class PriceData:
    """Normalized prices from one entity, plus anything that went wrong."""

    intervals: tuple[PriceInterval, ...]
    currency: str | None
    source: str
    problems: tuple[str, ...] = ()
    tomorrow_valid: bool | None = None
    """Whether tomorrow's settled prices have been published.

    ``None`` when the source says nothing either way, which is different from a
    stated ``False`` and must not be reported as one.
    """

    @property
    def quality(self) -> PlanPriceQuality | None:
        """Aggregate quality across all parsed intervals."""
        if not self.intervals:
            return None
        qualities = {interval.quality for interval in self.intervals}
        if qualities == {PriceQuality.ACTUAL}:
            return PlanPriceQuality.ACTUAL
        if qualities == {PriceQuality.FORECAST}:
            return PlanPriceQuality.FORECAST
        return PlanPriceQuality.MIXED

    @property
    def is_usable(self) -> bool:
        """Whether anything at all could be parsed."""
        return bool(self.intervals)


def _first_key(entry: Mapping[str, Any], candidates: Sequence[str]) -> object | None:
    """Return the first present key from a list of candidates."""
    for key in candidates:
        if key in entry:
            return entry[key]
    return None


def _count_entries(entries: object) -> int:
    """Count the entries in an attribute list, or zero if it isn't one."""
    if not isinstance(entries, Sequence) or isinstance(entries, str | bytes):
        return 0
    return len(entries)


def _parse_moment(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp, requiring it to carry an offset.

    A naive timestamp is rejected rather than assumed to be local. Price data
    without an offset cannot be placed on the timeline safely, and guessing is
    wrong by the UTC offset for half the year.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _price_multiplier(unit: object, use_cent: object) -> Decimal | None:
    """Work out what to multiply a raw price by to get a price per kWh.

    An unrecognised unit yields None so the caller can refuse. Assuming kWh when
    a sensor reports MWh makes every cost wrong by a factor of 1000 while leaving
    the chosen window unchanged, so the error would be invisible in the schedule.
    """
    factor = _UNIT_TO_PER_KWH.get(str(unit)) if unit is not None else Decimal(1)
    if factor is None:
        return None
    if use_cent:
        factor = factor / Decimal(100)
    return factor


def _entry_intervals(
    entries: object, quality: PriceQuality, multiplier: Decimal, problems: list[str]
) -> list[PriceInterval]:
    """Convert one attribute list into price intervals."""
    if not isinstance(entries, Sequence) or isinstance(entries, str | bytes):
        return []

    parsed: list[tuple[datetime, datetime | None, Decimal]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        start = _parse_moment(_first_key(entry, _START_KEYS))
        if start is None:
            continue
        raw_price = _first_key(entry, _PRICE_KEYS)
        try:
            price = Decimal(str(raw_price)) * multiplier
        except (InvalidOperation, TypeError, ValueError):
            continue
        parsed.append((start, _parse_moment(_first_key(entry, _END_KEYS)), price))

    if not parsed:
        return []

    parsed.sort(key=lambda item: to_utc(item[0]))

    intervals: list[PriceInterval] = []
    for index, (start, explicit_end, price) in enumerate(parsed):
        if explicit_end is not None:
            end = explicit_end
        elif index + 1 < len(parsed):
            end = parsed[index + 1][0]
        else:
            # The final entry has no successor to bound it. Reuse the previous
            # step so a 15-minute series is not silently extended to an hour.
            previous_step = (
                to_utc(parsed[index][0]) - to_utc(parsed[index - 1][0])
                if index > 0
                else _DEFAULT_STEP
            )
            end = start + (previous_step or _DEFAULT_STEP)

        if to_utc(end) <= to_utc(start):
            problems.append(f"discarded interval at {start.isoformat()}: end <= start")
            continue
        intervals.append(
            PriceInterval(start=start, end=end, price_per_kwh=price, quality=quality)
        )

    return intervals


def _merge(intervals: Sequence[PriceInterval]) -> tuple[PriceInterval, ...]:
    """Deduplicate by start instant, letting actual prices win over forecasts.

    Forecast data routinely overlaps the actual prices once tomorrow is
    published. Keeping both would double-count the period and, worse, could pick
    a window using a predicted price when the settled one is known.
    """
    by_start: dict[datetime, PriceInterval] = {}
    for interval in intervals:
        key = to_utc(interval.start)
        existing = by_start.get(key)
        if existing is None or (
            existing.quality is PriceQuality.FORECAST
            and interval.quality is PriceQuality.ACTUAL
        ):
            by_start[key] = interval
    return tuple(by_start[key] for key in sorted(by_start))


def parse_price_attributes(
    attributes: Mapping[str, Any], source: str = "price entity"
) -> PriceData:
    """Build normalized price intervals from a price entity's attributes.

    Recognises the forward-curve attributes used by Energi Data Service and
    similar integrations. The user selects an entity; working out how to read it
    is this function's job, never a configuration question.
    """
    problems: list[str] = []

    raw_tomorrow_valid = attributes.get("tomorrow_valid")
    tomorrow_valid = (
        bool(raw_tomorrow_valid) if raw_tomorrow_valid is not None else None
    )

    multiplier = _price_multiplier(
        attributes.get("unit"), attributes.get("use_cent", False)
    )
    if multiplier is None:
        return PriceData(
            intervals=(),
            currency=attributes.get("currency"),
            source=source,
            problems=(f"unsupported price unit {attributes.get('unit')!r}",),
            tomorrow_valid=tomorrow_valid,
        )

    collected: list[PriceInterval] = []
    for key in _ACTUAL_KEYS:
        if tomorrow_valid is False and key in _TOMORROW_KEYS:
            # Energi Data Service publishes tomorrow's settled prices in the
            # early afternoon and only then sets tomorrow_valid. Anything left
            # in the attribute before that is the previous day's data, so it is
            # discarded and the forecast covers the period instead. Treating it
            # as settled would plan a window on prices that never applied.
            if _count_entries(attributes.get(key)):
                problems.append(f"{key} ignored: tomorrow's prices are not published")
            continue
        collected.extend(
            _entry_intervals(
                attributes.get(key), PriceQuality.ACTUAL, multiplier, problems
            )
        )
    for key in _FORECAST_KEYS:
        collected.extend(
            _entry_intervals(
                attributes.get(key), PriceQuality.FORECAST, multiplier, problems
            )
        )

    if not collected:
        problems.append(
            "no price data found; expected one of "
            f"{', '.join(_ACTUAL_KEYS + _FORECAST_KEYS)}"
        )

    return PriceData(
        intervals=_merge(collected),
        currency=attributes.get("currency"),
        source=source,
        problems=tuple(problems),
        tomorrow_valid=tomorrow_valid,
    )
