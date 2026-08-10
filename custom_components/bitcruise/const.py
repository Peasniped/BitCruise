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

# Approval.
CONF_APPROVAL_POLICY: Final = "approval_policy"
CONF_MATERIAL_CHANGE_MINUTES: Final = "material_change_minutes"

# Charger execution capabilities. All optional: a charger may authorize itself,
# or have no pause, and an absent control is a capability this installation does
# not have rather than a failure.
CONF_CHARGER_STATUS_ENTITY: Final = "charger_status_entity"
CONF_CHARGER_ONLINE_ENTITY: Final = "charger_online_entity"
CONF_AUTHORIZATION_REQUIRED_ENTITY: Final = "authorization_required_entity"
CONF_AUTHORIZE_ENTITY: Final = "authorize_entity"
CONF_START_ENTITY: Final = "start_entity"
CONF_STOP_ENTITY: Final = "stop_entity"
CONF_CHARGING_POWER_ENTITY: Final = "charging_power_entity"

# Defaults. Charging power reflects a 16 A three-phase supply; see
# docs/reference-installation.md. It is configurable because chargers differ.
DEFAULT_CHARGING_POWER_KW: Final = 11.0
DEFAULT_CHARGING_EFFICIENCY: Final = 90.0
DEFAULT_RESERVE_FLOOR_PCT: Final = 0.0
DEFAULT_TARGET_PCT: Final = 80.0
DEFAULT_READY_BY: Final = "07:00:00"

# One price interval. A window that moves by less than this has shifted within a
# slot rather than to a different one, which is not worth asking about.
DEFAULT_MATERIAL_CHANGE_MINUTES: Final = 60

# Storage. The key is suffixed with the config entry id, so a future multi-entry
# installation does not have two planners writing the same file.
STORAGE_VERSION: Final = 1
