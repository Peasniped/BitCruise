"""Buttons for answering a proposed charging plan.

These call straight into the coordinator, which owns the approval rules. A
notification action in a later phase calls the same methods, so the rules are
never reimplemented anywhere.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import BitCruiseConfigEntry, BitCruiseCoordinator
from .entity import BitCruiseEntity


@dataclass(frozen=True, kw_only=True)
class BitCruiseButtonDescription(ButtonEntityDescription):
    """Describes a BitCruise button."""

    press_fn: Callable[[BitCruiseCoordinator], Awaitable[None]]
    needs_proposal: bool = False
    """Whether the action only means something while a proposal is pending."""


BUTTONS: tuple[BitCruiseButtonDescription, ...] = (
    BitCruiseButtonDescription(
        key="accept_plan",
        translation_key="accept_plan",
        press_fn=lambda coordinator: coordinator.async_accept(),
        needs_proposal=True,
    ),
    BitCruiseButtonDescription(
        key="reject_plan",
        translation_key="reject_plan",
        press_fn=lambda coordinator: coordinator.async_reject(),
        needs_proposal=True,
    ),
    BitCruiseButtonDescription(
        key="recalculate_plan",
        translation_key="recalculate_plan",
        press_fn=lambda coordinator: coordinator.async_recalculate(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BitCruiseConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the BitCruise buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        BitCruiseButton(coordinator, description) for description in BUTTONS
    )


class BitCruiseButton(BitCruiseEntity, ButtonEntity):
    """One approval action.

    Accept and reject are unavailable with nothing pending. A greyed-out button
    is the clearest signal available that nothing wants an answer, and a stale
    press failing visibly beats it silently doing nothing. Recalculate is always
    available, since reconsidering is meaningful at any time.
    """

    entity_description: BitCruiseButtonDescription

    def __init__(
        self,
        coordinator: BitCruiseCoordinator,
        description: BitCruiseButtonDescription,
    ) -> None:
        """Set up the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Whether pressing this button would do anything."""
        if not super().available:
            return False
        if not self.entity_description.needs_proposal:
            return True
        return self.coordinator.data.record.requires_approval

    async def async_press(self) -> None:
        """Run the action."""
        await self.entity_description.press_fn(self.coordinator)
