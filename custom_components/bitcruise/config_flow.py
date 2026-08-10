"""Config flow for the BitCruise integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
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


def settings_schema(
    defaults: dict[str, Any], sources: dict[str, Any] | None = None
) -> vol.Schema:
    """Build the settings schema, pre-filled with current values.

    The fixed target and capacity fields are omitted when the corresponding
    entity was selected, because they would then be dead inputs: the entity
    always wins, so offering the number invites the user to set a value that is
    silently ignored.
    """
    sources = sources or {}
    fields: dict[Any, Any] = {}

    if not sources.get(CONF_TARGET_ENTITY):
        fields[
            vol.Optional(
                CONF_TARGET_FIXED_PCT,
                default=defaults.get(CONF_TARGET_FIXED_PCT, DEFAULT_TARGET_PCT),
            )
        ] = _percentage()

    if not sources.get(CONF_CAPACITY_ENTITY):
        fields[
            vol.Optional(
                CONF_CAPACITY_FIXED_KWH,
                default=defaults.get(CONF_CAPACITY_FIXED_KWH, 0),
            )
        ] = NumberSelector(
            NumberSelectorConfig(min=0, max=500, step=0.001, unit_of_measurement="kWh")
        )

    fields.update(
        {
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
            # When to ask before charging is deliberately absent: it is
            # select.approval_policy, so there is one place it can be read and
            # one place it can be changed.
            vol.Required(
                CONF_MATERIAL_CHANGE_MINUTES,
                default=defaults.get(
                    CONF_MATERIAL_CHANGE_MINUTES, DEFAULT_MATERIAL_CHANGE_MINUTES
                ),
            ): NumberSelector(
                NumberSelectorConfig(min=1, max=360, step=1, unit_of_measurement="min")
            ),
        }
    )
    return vol.Schema(fields)


def validate_settings(
    sources: dict[str, Any],
    settings: dict[str, Any],
    current_target: float | None = None,
) -> dict[str, str]:
    """Return field errors for a settings submission.

    The floor may not exceed the target: the floor is a lower bound on being able
    to drive at all, not a second target. ``current_target`` is the value read
    from a selected target entity, so the clash can be caught during setup rather
    than only appearing as a problem once the entity is running.
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

    effective_target = current_target if has_target_entity else target
    floor = settings.get(CONF_RESERVE_FLOOR_PCT, 0)
    if effective_target is not None and floor > effective_target:
        errors[CONF_RESERVE_FLOOR_PCT] = "floor_above_target"

    return errors


def validate_sources(hass: HomeAssistant, sources: dict[str, Any]) -> dict[str, str]:
    """Return field errors for the selected source entities.

    Catches the charge target being confused with a charging *current* limit.
    Vehicle integrations expose both as plain numbers, and picking the wrong one
    produces a target of "32" that looks entirely reasonable.
    """
    errors: dict[str, str] = {}

    if entity_id := sources.get(CONF_TARGET_ENTITY):
        state = hass.states.get(entity_id)
        unit = state.attributes.get("unit_of_measurement") if state else None
        if unit is not None and unit != PERCENTAGE:
            errors[CONF_TARGET_ENTITY] = "target_not_a_percentage"

    return errors


def read_target_entity(hass: HomeAssistant, sources: dict[str, Any]) -> float | None:
    """Read the current value of a selected target entity, if it is readable."""
    entity_id = sources.get(CONF_TARGET_ENTITY)
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class BitCruiseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the BitCruise config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with no collected sources."""
        self._sources: dict[str, Any] = {}

    @property
    def _reconfigure_entry(self) -> ConfigEntry | None:
        """The entry being reconfigured, or None during initial setup."""
        if self.source != SOURCE_RECONFIGURE:
            return None
        return self._get_reconfigure_entry()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the source entities during initial setup."""
        return await self._async_step_sources(user_input, "user")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the selected entities without deleting the integration.

        Picking the wrong entity is easy, and a vehicle integration can rename or
        replace one. Forcing a delete and re-add to recover would also discard
        every setting.
        """
        return await self._async_step_sources(user_input, "reconfigure")

    async def _async_step_sources(
        self, user_input: dict[str, Any] | None, step_id: str
    ) -> ConfigFlowResult:
        """Show and validate the source entity form."""
        entry = self._reconfigure_entry
        suggestions = dict(entry.data) if entry else {}

        if user_input is None:
            return self.async_show_form(
                step_id=step_id,
                data_schema=self.add_suggested_values_to_schema(
                    SOURCES_SCHEMA, suggestions
                ),
            )

        if errors := validate_sources(self.hass, user_input):
            return self.async_show_form(
                step_id=step_id,
                data_schema=self.add_suggested_values_to_schema(
                    SOURCES_SCHEMA, user_input
                ),
                errors=errors,
            )

        self._sources = user_input
        return await self.async_step_settings()

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the charging settings."""
        entry = self._reconfigure_entry
        defaults = dict(entry.options) if entry else {}

        if user_input is None:
            return self.async_show_form(
                step_id="settings",
                data_schema=settings_schema(defaults, self._sources),
            )

        errors = validate_settings(
            self._sources, user_input, read_target_entity(self.hass, self._sources)
        )
        if errors:
            return self.async_show_form(
                step_id="settings",
                data_schema=settings_schema(user_input, self._sources),
                errors=errors,
            )

        if entry is not None:
            return self.async_update_reload_and_abort(
                entry, data=self._sources, options=user_input
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
        sources = dict(self.config_entry.data)
        current = {**sources, **self.config_entry.options}

        if user_input is None:
            return self.async_show_form(
                step_id="init", data_schema=settings_schema(current, sources)
            )

        errors = validate_settings(
            sources, user_input, read_target_entity(self.hass, sources)
        )
        if errors:
            return self.async_show_form(
                step_id="init",
                data_schema=settings_schema(user_input, sources),
                errors=errors,
            )

        return self.async_create_entry(data=user_input)
