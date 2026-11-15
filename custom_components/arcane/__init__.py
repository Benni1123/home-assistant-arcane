"""Arcane integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcaneApiClient
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    container_key,
    is_ephemeral_container,
)
from .coordinator import ArcaneCoordinator
from .entity import compose_project
from .services import async_register_services
from .stale import async_setup_stale_cleanup

_LOGGER = logging.getLogger(__name__)

type ArcaneConfigEntry = ConfigEntry[ArcaneCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ArcaneConfigEntry) -> bool:
    """Set up Arcane from a config entry."""
    client = ArcaneApiClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_API_KEY],
        entry.data.get(CONF_VERIFY_SSL, True),
    )
    coordinator = ArcaneCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    async_register_services(hass)
    _async_remove_ephemeral_registry_entries(hass, entry, coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_stale_cleanup(hass, entry, coordinator)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArcaneConfigEntry) -> bool:
    """Unload Arcane."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ArcaneConfigEntry) -> None:
    """Reload Arcane after options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_remove_ephemeral_registry_entries(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    coordinator: ArcaneCoordinator,
) -> None:
    """Remove entities and devices left behind by short-lived helper containers."""
    prefix = f"{coordinator.environment_id}_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        unique_id = entity.unique_id
        if not unique_id.startswith(prefix):
            continue
        key = container_key(unique_id[len(prefix) :])
        if key and is_ephemeral_container(key):
            _LOGGER.debug("Removing stale Arcane entity %s", entity.entity_id)
            entity_registry.async_remove(entity.entity_id)

    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN or not identifier.startswith(prefix):
                continue
            if is_ephemeral_container(identifier[len(prefix) :]):
                _LOGGER.debug("Removing stale Arcane device %s", device.name)
                device_registry.async_update_device(
                    device.id, remove_config_entry_id=entry.entry_id
                )
                break


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: ArcaneConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting devices of containers that no longer exist."""
    coordinator: ArcaneCoordinator = entry.runtime_data
    environment_id = coordinator.environment_id
    containers = coordinator.data.containers.values() if coordinator.data else []
    active = {environment_id} | {
        f"{environment_id}_{compose_project(container)}" for container in containers
    }
    return not any(
        domain == DOMAIN and identifier in active
        for domain, identifier in device_entry.identifiers
    )
