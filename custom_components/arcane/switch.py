"""Power switch for Arcane containers."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneCoordinator
from .entity import ArcaneEntity, container_device_info
from .lifecycle import ON_STATES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ArcaneCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one power switch for every discovered container."""
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
            ArcaneContainerSwitch(coordinator, key) for key in new_keys
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))
    entry.async_on_unload(coordinator.async_add_forget_listener(known.difference_update))


class ArcaneContainerSwitch(ArcaneEntity, SwitchEntity):
    """Start or stop one container; the state follows Arcane's container state."""

    _attr_icon = "mdi:power"

    def __init__(self, coordinator: ArcaneCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.environment_id}_{key}_power"

        containers = coordinator.data.containers if coordinator.data else {}
        container = containers.get(key) or {}
        self._attr_device_info, container_label = container_device_info(
            coordinator, key, container
        )
        if container_label:
            self._attr_translation_key = "container_power_named"
            self._attr_translation_placeholders = {"container": container_label}
        else:
            self._attr_translation_key = "container_power"

    @property
    def _container(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.containers.get(self._key)

    @property
    def _state(self) -> str | None:
        container = self._container
        return container.get("state") if container else None

    @property
    def available(self) -> bool:
        """Return whether the container still exists."""
        return super().available and self._container is not None

    @property
    def is_on(self) -> bool | None:
        """Return whether the container is running, restarting or paused."""
        state = self._state
        return None if state is None else state in ON_STATES

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the exact Docker state, such as paused or exited."""
        return {"container_state": self._state}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start a stopped container, or resume a paused one."""
        state = self._state
        if state == "paused":
            await self.coordinator.async_unpause_container(self._key)
        elif state not in ON_STATES:
            await self.coordinator.async_start_container(self._key)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop a container that is running, restarting or paused."""
        if self._state in ON_STATES:
            await self.coordinator.async_stop_container(self._key)
