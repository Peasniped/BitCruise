"""The BitCruise integration.

BitCruise plans and executes residential EV charging from entities that already
exist in Home Assistant. This module wires a config entry to its coordinator and
platforms; all planning logic lives in the pure planner.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import BitCruiseConfigEntry, BitCruiseCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: BitCruiseConfigEntry) -> bool:
    """Set up BitCruise from a config entry."""
    coordinator = BitCruiseCoordinator(hass, entry)
    await coordinator.async_load_stored_state()
    # Listeners go up before the first evaluation, not after. Source
    # integrations are often still starting, and an entity that appeared in
    # between would otherwise go unnoticed until something else changed.
    await coordinator.async_setup_listeners()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BitCruiseConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: BitCruiseConfigEntry) -> None:
    """Delete the persisted approval record along with the entry."""
    await BitCruiseCoordinator(hass, entry).async_remove_stored_state()


async def _async_reload_entry(hass: HomeAssistant, entry: BitCruiseConfigEntry) -> None:
    """Reload when options change, so new settings take effect immediately."""
    await hass.config_entries.async_reload(entry.entry_id)
