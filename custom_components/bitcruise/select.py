"""How much BitCruise asks before charging, as a control rather than a setting.

This used to live in the options flow. It moved because it is the one setting a
household actually changes with the seasons — asked every evening while prices
are volatile, left alone once the pattern is trusted — and burying that three
dialogs deep made "stop asking me" harder than it needed to be.

The entity is the only source of truth for the policy. It is persisted with the
approval record so the coordinator knows it before the first evaluation.
"""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BitCruiseConfigEntry, BitCruiseCoordinator
from .entity import BitCruiseEntity
from .models import ApprovalPolicy


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BitCruiseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BitCruise selects."""
    async_add_entities([BitCruiseApprovalPolicy(entry.runtime_data)])


class BitCruiseApprovalPolicy(BitCruiseEntity, SelectEntity):
    """When to ask before charging."""

    _attr_options: ClassVar[list[str]] = [policy.value for policy in ApprovalPolicy]

    def __init__(self, coordinator: BitCruiseCoordinator) -> None:
        """Set up the select."""
        super().__init__(coordinator, "approval_policy")

    @property
    def current_option(self) -> str:
        """The policy in force."""
        return self.coordinator.data.approval_policy.value

    async def async_select_option(self, option: str) -> None:
        """Change the policy and reconcile against it immediately."""
        await self.coordinator.async_set_approval_policy(ApprovalPolicy(option))
