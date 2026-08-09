"""Constants for the BitCruise integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "bitcruise"

# Vehicle sources.
CONF_SOC_ENTITY: Final = "soc_entity"
CONF_TARGET_ENTITY: Final = "target_entity"
CONF_TARGET_FIXED_PCT: Final = "target_fixed_pct"
CONF_CAPACITY_ENTITY: Final = "capacity_entity"
CONF_CAPACITY_FIXED_KWH: Final = "capacity_fixed_kwh"
CONF_PLUG_ENTITY: Final = "plug_entity"
CONF_AVAILABILITY_ENTITY: Final = "availability_entity"

# Charging parameters.
CONF_CHARGING_POWER_KW: Final = "charging_power_kw"
CONF_CHARGING_EFFICIENCY: Final = "charging_efficiency"
CONF_RESERVE_FLOOR_PCT: Final = "reserve_floor_pct"
CONF_READY_BY: Final = "ready_by"
CONF_NOT_BEFORE: Final = "not_before"

# Prices.
CONF_PRICE_ENTITY: Final = "price_entity"

# Defaults. Charging power reflects a 16 A three-phase supply; see
# docs/reference-installation.md. It is configurable because chargers differ.
DEFAULT_CHARGING_POWER_KW: Final = 11.0
DEFAULT_CHARGING_EFFICIENCY: Final = 90.0
DEFAULT_RESERVE_FLOOR_PCT: Final = 0.0
DEFAULT_TARGET_PCT: Final = 80.0
DEFAULT_READY_BY: Final = "07:00:00"

# A waking car updates state of charge, plug status and availability within the
# same second. The first change is applied immediately so the UI stays
# responsive; anything arriving during the cooldown is coalesced into one
# follow-up recomputation rather than replanning per event (DESIGN.md 6).
REPLAN_DEBOUNCE_SECONDS: Final = 5.0
