"""Shared Arcane entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArcaneCoordinator


class ArcaneEntity(CoordinatorEntity[ArcaneCoordinator]):
    """Base entity tied to one Arcane environment."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ArcaneCoordinator) -> None:
        super().__init__(coordinator)
        version = coordinator.data.version if coordinator.data else {}
        docker_info = coordinator.data.docker_info if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.environment_id)},
            name=f"Arcane {coordinator.environment_name}",
            manufacturer="Arcane",
            model="Docker environment",
            sw_version=version.get("displayVersion") or version.get("currentVersion"),
            hw_version=docker_info.get("ServerVersion"),
            configuration_url=coordinator.client.base_url,
        )
