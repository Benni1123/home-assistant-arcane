"""Action select for Arcane containers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneCoordinator
from .entity import ArcaneEntity, container_device_info
from .lifecycle import available_actions


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ArcaneCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one action select for every discovered container."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_entities() -> None:
        keys = set(coordinator.data.containers) if coordinator.data else set()
        new_keys = sorted(keys - known)
        if not new_keys:
            return
        known.update(new_keys)
        async_add_entities(
            ArcaneContainerActionSelect(coordinator, key) for key in new_keys
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))
    entry.async_on_unload(coordinator.async_add_forget_listener(known.difference_update))


class ArcaneContainerActionSelect(ArcaneEntity, SelectEntity):
    """Offer only the lifecycle actions that fit the container's current state."""

    _attr_icon = "mdi:play-box-multiple-outline"

    def __init__(self, coordinator: ArcaneCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.environment_id}_{key}_control"

        containers = coordinator.data.containers if coordinator.data else {}
        container = containers.get(key) or {}
        self._attr_device_info, container_label = container_device_info(
            coordinator, key, container
        )
        if container_label:
            self._attr_translation_key = "container_control_named"
            self._attr_translation_placeholders = {"container": container_label}
        else:
            self._attr_translation_key = "container_control"

    @property
    def _container(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.containers.get(self._key)

    @property
    def available(self) -> bool:
        """Return whether the container still exists."""
        return super().available and self._container is not None

    @property
    def options(self) -> list[str]:
        """Return the actions possible in the container's current state."""
        container = self._container
        return available_actions(container.get("state") if container else None)

    @property
    def current_option(self) -> str | None:
        """Actions are triggered, not states, so no option stays selected."""
        return None

    async def async_select_option(self, option: str) -> None:
        """Run the chosen action once, never repeating it automatically."""
        if option not in self.options:
            raise ServiceValidationError(
                f"{option} is not possible in the container's current state"
            )
        actions = {
            "start": self.coordinator.async_start_container,
            "stop": self.coordinator.async_stop_container,
            "restart": self.coordinator.async_restart_container,
            "pause": self.coordinator.async_pause_container,
            "resume": self.coordinator.async_unpause_container,
        }
        await actions[option](self._key)
