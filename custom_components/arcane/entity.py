"""Shared Arcane entity helpers."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ArcaneCoordinator, container_name

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


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


def compose_project(container: dict[str, Any]) -> str:
    """Return the Compose project name, falling back to the container name."""
    labels = container.get("labels") or {}
    project = labels.get(COMPOSE_PROJECT_LABEL) if isinstance(labels, dict) else None
    return str(project) if project else container_name(container)


def container_device_info(
    coordinator: ArcaneCoordinator, key: str, container: dict[str, Any]
) -> tuple[DeviceInfo, str | None]:
    """Return the device info and disambiguating name for one container entity."""
    containers = coordinator.data.containers if coordinator.data else {}
    project = compose_project(container) if container else key
    siblings = [
        other for other in containers.values() if compose_project(other) == project
    ]

    if len(siblings) > 1:
        # Multi-service stack: one device per project, entities per container.
        device_name = project
        model = "Compose project"
        name = key
    else:
        # Single container: the project name adds nothing.
        device_name = key
        model = str(container.get("image") or "Docker container")
        name = None

    device_info = DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.environment_id}_{project}")},
        name=device_name,
        manufacturer="Arcane",
        model=model,
        via_device=(DOMAIN, coordinator.environment_id),
        configuration_url=f"{coordinator.client.base_url}/containers",
    )
    return device_info, name
