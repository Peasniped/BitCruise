"""Config flow for the BitCruise integration.

This is the Phase 0 skeleton. It creates a single entry with no configuration so
that setup and unload can be verified end to end. The vehicle, charging, and
price selections described in DESIGN.md section 10 are added in Phase 2.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class BitCruiseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the BitCruise config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(step_id="user")

        return self.async_create_entry(title="BitCruise", data={})
