"""Buttons for Arcane."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneCoordinator
from .entity import ArcaneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ArcaneCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Arcane update scan button."""
    async_add_entities([ArcaneCheckUpdatesButton(entry.runtime_data)])


class ArcaneCheckUpdatesButton(ArcaneEntity, ButtonEntity):
    """Trigger a complete Arcane registry scan."""

    _attr_translation_key = "check_updates"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: ArcaneCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.environment_id}_check_updates"

    async def async_press(self) -> None:
        """Check every image for a newer digest."""
        await self.coordinator.async_check_updates()
