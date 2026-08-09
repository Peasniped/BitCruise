"""Persist the approval record between Home Assistant restarts.

Scheduled callbacks do not survive a restart, so the approved plan cannot live
only in memory (DESIGN.md section 11). This module is only the Home Assistant
storage plumbing; what gets written and how it is parsed belongs to
``plan_state``, which stays importable without Home Assistant.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .plan_state import PlanRecord, stored_state_from_dict, stored_state_to_dict


class PlanStore:
    """The persisted approval state for one config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Open the store for a config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )

    async def async_load(self) -> tuple[PlanRecord, bool]:
        """Read the record and the smart-charging switch.

        A missing or unreadable store yields an empty record rather than an
        error: forgetting an approval is recoverable, failing to set up is not.
        """
        return stored_state_from_dict(await self._store.async_load())

    async def async_save(self, record: PlanRecord, *, smart_charging: bool) -> None:
        """Write the record."""
        await self._store.async_save(
            stored_state_to_dict(record, smart_charging=smart_charging)
        )

    async def async_remove(self) -> None:
        """Delete the store, when the config entry is removed."""
        await self._store.async_remove()
