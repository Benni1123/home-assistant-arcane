"""Data coordinator for Arcane."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcaneApiClient, ArcaneApiError
from .const import (
    CONF_ENVIRONMENT_ID,
    CONF_ENVIRONMENT_NAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    update_interval,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ArcaneData:
    """Combined Arcane state used by all platforms."""

    summary: dict[str, Any]
    dashboard: dict[str, Any]
    containers: dict[str, dict[str, Any]]
    container_counts: dict[str, Any]
    image_counts: dict[str, Any]
    volume_counts: dict[str, Any]
    network_counts: dict[str, Any]
    project_counts: dict[str, Any]
    port_count: int | None
    version: dict[str, Any]
    docker_info: dict[str, Any]
    image_updates: dict[str, dict[str, Any]] = field(default_factory=dict)


def container_name(container: dict[str, Any]) -> str:
    """Return a stable, human-readable name."""
    names = container.get("names") or []
    if names:
        return str(names[0]).lstrip("/")
    return str(container.get("id", "unknown"))[:12]


class ArcaneCoordinator(DataUpdateCoordinator[ArcaneData]):
    """Coordinate Arcane polling and actions."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ArcaneApiClient,
    ) -> None:
        self.client = client
        self.environment_id = str(entry.data[CONF_ENVIRONMENT_ID])
        self.environment_name = str(entry.data[CONF_ENVIRONMENT_NAME])
        interval = int(
            entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{self.environment_id}",
            update_interval=update_interval(interval),
            config_entry=entry,
        )

    async def _async_update_data(self) -> ArcaneData:
        try:
            summary, containers_list = await asyncio.gather(
                self.client.async_get_update_summary(self.environment_id),
                self.client.async_list_containers(self.environment_id),
            )
        except ArcaneApiError as err:
            raise UpdateFailed(str(err)) from err

        image_refs = [
            str(container.get("image"))
            for container in containers_list
            if container.get("image")
        ]
        try:
            image_updates = await self.client.async_get_updates_by_refs(
                self.environment_id, image_refs
            )
        except ArcaneApiError as err:
            _LOGGER.debug("Arcane image-update records unavailable: %s", err)
            image_updates = {}

        optional_calls = (
            self.client.async_get_dashboard(self.environment_id),
            self.client.async_get_container_counts(self.environment_id),
            self.client.async_get_image_counts(self.environment_id),
            self.client.async_get_volume_counts(self.environment_id),
            self.client.async_get_network_counts(self.environment_id),
            self.client.async_get_project_counts(self.environment_id),
            self.client.async_get_port_count(self.environment_id),
            self.client.async_get_version(self.environment_id),
            self.client.async_get_docker_info(self.environment_id),
        )
        results = await asyncio.gather(*optional_calls, return_exceptions=True)
        previous = self.data

        def value(index: int, fallback: Any) -> Any:
            result = results[index]
            if isinstance(result, BaseException):
                _LOGGER.debug("Optional Arcane statistic unavailable: %s", result)
                return fallback
            return result

        containers: dict[str, dict[str, Any]] = {}
        for container in containers_list:
            record = image_updates.get(str(container.get("image", "")))
            if record:
                # The embedded updateInfo is a stale cache; by-refs is current.
                container["updateInfo"] = dict(record)
            containers[container_name(container)] = container

        return ArcaneData(
            summary=summary,
            dashboard=value(0, previous.dashboard if previous else {}),
            containers=containers,
            container_counts=value(1, previous.container_counts if previous else {}),
            image_counts=value(2, previous.image_counts if previous else {}),
            volume_counts=value(3, previous.volume_counts if previous else {}),
            network_counts=value(4, previous.network_counts if previous else {}),
            project_counts=value(5, previous.project_counts if previous else {}),
            port_count=value(6, previous.port_count if previous else None),
            version=value(7, previous.version if previous else {}),
            docker_info=value(8, previous.docker_info if previous else {}),
            image_updates=image_updates,
        )

    async def async_check_updates(self) -> None:
        """Run a registry scan, then refresh HA state."""
        await self.client.async_check_all_updates(self.environment_id)
        await self.async_request_refresh()

    async def async_install_update(self, key: str) -> None:
        """Update the current container represented by a stable key."""
        container = self.data.containers.get(key) if self.data else None
        if container is None:
            raise ArcaneApiError(f"Container {key} is no longer available")
        await self.client.async_update_container(
            self.environment_id, str(container["id"])
        )
        await self.async_request_refresh()
