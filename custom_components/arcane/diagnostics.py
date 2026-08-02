"""Diagnostics for Arcane."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY
from .coordinator import ArcaneCoordinator

TO_REDACT = {CONF_API_KEY, "apiKey", "authUsername"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[ArcaneCoordinator]
) -> dict[str, Any]:
    """Return redacted diagnostics."""
    coordinator = entry.runtime_data
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "summary": coordinator.data.summary if coordinator.data else {},
            "dashboard": coordinator.data.dashboard if coordinator.data else {},
            "statistics": {
                "containers": coordinator.data.container_counts,
                "images": coordinator.data.image_counts,
                "volumes": coordinator.data.volume_counts,
                "networks": coordinator.data.network_counts,
                "projects": coordinator.data.project_counts,
                "published_ports": coordinator.data.port_count,
                "version": coordinator.data.version,
                "docker": coordinator.data.docker_info,
            }
            if coordinator.data
            else {},
            "containers": list(
                coordinator.data.containers.values() if coordinator.data else []
            ),
        },
        TO_REDACT,
    )
