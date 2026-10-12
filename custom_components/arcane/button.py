"""Buttons for Arcane."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneCoordinator
from .entity import ArcaneEntity, container_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ArcaneCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Arcane update scan button and per-container control buttons."""
    coordinator = entry.runtime_data
    async_add_entities([ArcaneCheckUpdatesButton(coordinator)])

    known: set[str] = set()

    @callback
    def add_new_entities() -> None:
        keys = set(coordinator.data.containers) if coordinator.data else set()
        new_keys = sorted(keys - known)
        if not new_keys:
            return
        known.update(new_keys)
        async_add_entities(
            entity_cls(coordinator, key)
            for key in new_keys
            for entity_cls in (
                ArcaneContainerPauseButton,
                ArcaneContainerResumeButton,
                ArcaneContainerRestartButton,
                ArcaneContainerStopButton,
            )
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


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


class ArcaneContainerActionButton(ArcaneEntity, ButtonEntity):
    """Base button that runs one lifecycle action on a container."""

    _action: str

    def __init__(self, coordinator: ArcaneCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.environment_id}_{key}_{self._action}"

        containers = coordinator.data.containers if coordinator.data else {}
        container = containers.get(key) or {}
        self._attr_device_info, container_label = container_device_info(
            coordinator, key, container
        )
        if container_label:
            self._attr_translation_key = f"{self._action}_named"
            self._attr_translation_placeholders = {"container": container_label}
        else:
            self._attr_translation_key = self._action

    @property
    def _container(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.containers.get(self._key)

    @property
    def available(self) -> bool:
        """Return whether the container still exists."""
        return super().available and self._container is not None


class ArcaneContainerPauseButton(ArcaneContainerActionButton):
    """Pause a running container."""

    _action = "pause"
    _attr_icon = "mdi:pause"

    async def async_press(self) -> None:
        """Pause this container."""
        await self.coordinator.async_pause_container(self._key)


class ArcaneContainerResumeButton(ArcaneContainerActionButton):
    """Resume a paused container."""

    _action = "resume"
    _attr_icon = "mdi:play"

    async def async_press(self) -> None:
        """Resume this container."""
        await self.coordinator.async_unpause_container(self._key)


class ArcaneContainerRestartButton(ArcaneContainerActionButton):
    """Restart a container."""

    _action = "restart"
    _attr_icon = "mdi:restart"
    _attr_device_class = ButtonDeviceClass.RESTART

    async def async_press(self) -> None:
        """Restart this container."""
        await self.coordinator.async_restart_container(self._key)


class ArcaneContainerStopButton(ArcaneContainerActionButton):
    """Stop a running container."""

    _action = "stop"
    _attr_icon = "mdi:stop"

    async def async_press(self) -> None:
        """Stop this container."""
        await self.coordinator.async_stop_container(self._key)
