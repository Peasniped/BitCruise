"""Normalize values read from user-selected Home Assistant entities.

Pure Python. Home Assistant is not imported here, and unit strings are written
out literally rather than pulled from ``homeassistant.const``, so the whole module
is testable without a running instance. The caller reads ``state.state`` and
``state.attributes`` and passes the raw values in.

Selected entities differ wildly between installations. Normalizing centrally is
what keeps string comparisons such as ``"connected"`` out of the rest of the code.
"""

from __future__ import annotations

from enum import StrEnum

# States Home Assistant uses for "no usable value". Written literally to keep this
# module free of Home Assistant imports.
STATE_UNKNOWN = "unknown"
STATE_UNAVAILABLE = "unavailable"
_NON_VALUES = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE, "none", ""})

_ENERGY_TO_KWH: dict[str, float] = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
_POWER_TO_KW: dict[str, float] = {"W": 0.001, "kW": 1.0, "MW": 1000.0}

# Plug states seen in the wild. The Volvo integration exposes an enum sensor with
# connected / disconnected / fault rather than a binary_sensor.
_CONNECTED_STATES = frozenset({"connected", "on", "true", "plugged", "plugged_in"})
_DISCONNECTED_STATES = frozenset({"disconnected", "off", "false", "unplugged"})
_FAULT_STATES = frozenset({"fault", "error", "failed"})

# Vehicle availability states that mean readings may be stale rather than current.
_STALE_AVAILABILITY_STATES = frozenset(
    {"no_internet", "power_saving_mode", "ota_installation_in_progress"}
)


class SourceUnavailable(Exception):
    """A selected source entity has no usable value right now.

    Carries the entity id and a human-readable reason so the integration can say
    which input is missing instead of failing anonymously.
    """

    def __init__(self, entity_id: str, reason: str) -> None:
        """Record which entity failed and why."""
        super().__init__(f"{entity_id}: {reason}")
        self.entity_id = entity_id
        self.reason = reason


class PlugStatus(StrEnum):
    """Normalized charging-cable state.

    ``FAULT`` is deliberately distinct from ``DISCONNECTED``: a charging fault is
    actionable and would be hidden if folded into "not connected".
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAULT = "fault"
    UNKNOWN = "unknown"


class DataFreshness(StrEnum):
    """Whether readings from the vehicle can be trusted as current.

    A car in power-saving mode or without connectivity keeps reporting its last
    known state of charge. Planning on that is the class of mistake DESIGN.md 3.4
    requires be refused rather than guessed at.
    """

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


def _require_value(entity_id: str, raw: str | float | None) -> str:
    """Return the raw state as a string, rejecting unknown and unavailable."""
    if raw is None:
        raise SourceUnavailable(entity_id, "entity not found")
    text = str(raw).strip()
    if text.lower() in _NON_VALUES:
        raise SourceUnavailable(entity_id, f"state is {text or 'empty'}")
    return text


def normalize_number(entity_id: str, raw: str | float | None) -> float:
    """Parse a numeric state, rejecting anything unusable."""
    text = _require_value(entity_id, raw)
    try:
        return float(text)
    except ValueError as err:
        raise SourceUnavailable(entity_id, f"state {text!r} is not numeric") from err


def normalize_percentage(entity_id: str, raw: str | float | None) -> float:
    """Parse a percentage and require it to be within 0..100."""
    value = normalize_number(entity_id, raw)
    if not 0.0 <= value <= 100.0:
        raise SourceUnavailable(entity_id, f"percentage {value} outside 0..100")
    return value


def normalize_energy_kwh(
    entity_id: str, raw: str | float | None, unit: str | None
) -> float:
    """Convert an energy reading to kWh.

    An unknown unit is refused rather than assumed. Guessing kWh when a sensor
    reports Wh understates capacity by a factor of 1000, and the resulting plan
    looks entirely plausible.
    """
    value = normalize_number(entity_id, raw)
    if unit is None:
        raise SourceUnavailable(entity_id, "no unit_of_measurement")
    if unit not in _ENERGY_TO_KWH:
        raise SourceUnavailable(entity_id, f"unsupported energy unit {unit!r}")
    return value * _ENERGY_TO_KWH[unit]


def normalize_power_kw(
    entity_id: str, raw: str | float | None, unit: str | None
) -> float:
    """Convert a power reading to kW."""
    value = normalize_number(entity_id, raw)
    if unit is None:
        raise SourceUnavailable(entity_id, "no unit_of_measurement")
    if unit not in _POWER_TO_KW:
        raise SourceUnavailable(entity_id, f"unsupported power unit {unit!r}")
    return value * _POWER_TO_KW[unit]


def normalize_plug_status(raw: str | None) -> PlugStatus:
    """Map a plug or cable state onto PlugStatus.

    Accepts both binary_sensor states and the enum sensors some vehicle
    integrations use instead.
    """
    if raw is None:
        return PlugStatus.UNKNOWN
    text = str(raw).strip().lower()
    if text in _CONNECTED_STATES:
        return PlugStatus.CONNECTED
    if text in _DISCONNECTED_STATES:
        return PlugStatus.DISCONNECTED
    if text in _FAULT_STATES:
        return PlugStatus.FAULT
    return PlugStatus.UNKNOWN


def normalize_freshness(raw: str | None) -> DataFreshness:
    """Decide whether vehicle readings are current, from an availability entity."""
    if raw is None:
        return DataFreshness.UNKNOWN
    text = str(raw).strip().lower()
    if text in _NON_VALUES:
        return DataFreshness.UNKNOWN
    if text in _STALE_AVAILABILITY_STATES:
        return DataFreshness.STALE
    return DataFreshness.FRESH
