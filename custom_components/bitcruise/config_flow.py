"""Config flow for the BitCruise integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TimeSelector,
)

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
    DEFAULT_TARGET_PCT,
    DOMAIN,
)


def _percentage(minimum: float = 0, maximum: float = 100) -> NumberSelector:
    """Build a percentage slider."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=NumberSelectorMode.SLIDER
        )
    )


SOURCES_SCHEMA = vol.Schema(
    {
        # State of charge is the one input where device_class is reliably set,
        # so it is safe to filter on. The others are filtered by domain only:
        # the Volvo charge target, for instance, carries no device class at all,
        # and filtering it out would make the integration unusable.
        vol.Required(CONF_SOC_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="battery")
        ),
        vol.Optional(CONF_TARGET_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "number", "input_number"])
        ),
        vol.Optional(CONF_CAPACITY_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["sensor", "number", "input_number"])
        ),
        vol.Optional(CONF_PLUG_ENTITY): EntitySelector(
            EntitySelectorConfig(domain=["binary_sensor", "sensor"])
        ),
        vol.Optional(CONF_AVAILABILITY_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="sensor")
        ),
        vol.Optional(CONF_PRICE_ENTITY): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="monetary")
        ),
    }
)


def settings_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the settings schema, pre-filled with current values."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_TARGET_FIXED_PCT,
                default=defaults.get(CONF_TARGET_FIXED_PCT, DEFAULT_TARGET_PCT),
            ): _percentage(),
            vol.Optional(
                CONF_CAPACITY_FIXED_KWH,
                default=defaults.get(CONF_CAPACITY_FIXED_KWH, 0),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=500, step=0.001, unit_of_measurement="kWh"
                )
            ),
            vol.Required(
                CONF_CHARGING_POWER_KW,
                default=defaults.get(CONF_CHARGING_POWER_KW, DEFAULT_CHARGING_POWER_KW),
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=50, step=0.1, unit_of_measurement="kW")
            ),
            vol.Required(
                CONF_CHARGING_EFFICIENCY,
                default=defaults.get(
                    CONF_CHARGING_EFFICIENCY, DEFAULT_CHARGING_EFFICIENCY
                ),
            ): _percentage(minimum=50),
            vol.Required(
                CONF_RESERVE_FLOOR_PCT,
                default=defaults.get(CONF_RESERVE_FLOOR_PCT, DEFAULT_RESERVE_FLOOR_PCT),
            ): _percentage(),
            vol.Required(
                CONF_READY_BY,
                default=defaults.get(CONF_READY_BY, DEFAULT_READY_BY),
            ): TimeSelector(),
            vol.Optional(
                CONF_NOT_BEFORE,
                description={"suggested_value": defaults.get(CONF_NOT_BEFORE)},
            ): TimeSelector(),
        }
    )


def validate_settings(
    sources: dict[str, Any], settings: dict[str, Any]
) -> dict[str, str]:
    """Return field errors for a settings submission.

    The floor may not exceed the target: the floor is a lower bound on being able
    to drive at all, not a second target. Silently reordering them would produce
    a plan the user did not ask for.
    """
    errors: dict[str, str] = {}

    has_target_entity = bool(sources.get(CONF_TARGET_ENTITY))
    target = settings.get(CONF_TARGET_FIXED_PCT)
    if not has_target_entity and target is None:
        errors[CONF_TARGET_FIXED_PCT] = "target_required"

    has_capacity_entity = bool(sources.get(CONF_CAPACITY_ENTITY))
    capacity = settings.get(CONF_CAPACITY_FIXED_KWH) or 0
    if not has_capacity_entity and capacity <= 0:
        errors[CONF_CAPACITY_FIXED_KWH] = "capacity_required"

    floor = settings.get(CONF_RESERVE_FLOOR_PCT, 0)
    if not has_target_entity and target is not None and floor > target:
        errors[CONF_RESERVE_FLOOR_PCT] = "floor_above_target"

    return errors


class BitCruiseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the BitCruise config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with no collected sources."""
        self._sources: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the source entities."""
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=SOURCES_SCHEMA)

        self._sources = user_input
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the charging settings."""
        if user_input is None:
            return self.async_show_form(
                step_id="settings", data_schema=settings_schema({})
            )

        errors = validate_settings(self._sources, user_input)
        if errors:
            return self.async_show_form(
                step_id="settings",
                data_schema=settings_schema(user_input),
                errors=errors,
            )

        return self.async_create_entry(
            title="BitCruise", data=self._sources, options=user_input
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BitCruiseOptionsFlow:
        """Return the options flow."""
        return BitCruiseOptionsFlow()


class BitCruiseOptionsFlow(OptionsFlow):
    """Adjust settings without reconfiguring the source entities."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the adjustable settings."""
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is None:
            return self.async_show_form(
                step_id="init", data_schema=settings_schema(current)
            )

        errors = validate_settings(dict(self.config_entry.data), user_input)
        if errors:
            return self.async_show_form(
                step_id="init",
                data_schema=settings_schema(user_input),
                errors=errors,
            )

        return self.async_create_entry(data=user_input)
