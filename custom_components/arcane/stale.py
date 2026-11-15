"""Remove Home Assistant devices of containers that stay gone."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_STALE_REMOVAL_HOURS,
    DEFAULT_STALE_REMOVAL_HOURS,
    DOMAIN,
    container_key,
)

if TYPE_CHECKING:
    from .coordinator import ArcaneCoordinator

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


class StaleContainerCleaner:
    """Count how long containers are missing and remove their registry entries."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        environment_id: str,
        hours: int,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._environment_id = environment_id
        self._delay = timedelta(hours=hours) if hours > 0 else None
        self._store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_stale_{entry.entry_id}")
        self._missing: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        self._now = dt_util.utcnow

    @property
    def busy(self) -> bool:
        """Return whether a cleanup pass is already running."""
        return self._lock.locked()

    async def async_load(self) -> None:
        """Restore first-seen-missing times so a restart does not reset the clock."""
        data = await self._store.async_load()
        stored = (data or {}).get("missing") if isinstance(data, dict) else None
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            try:
                parsed = dt_util.parse_datetime(value) if isinstance(value, str) else None
            except ValueError:
                parsed = None
            if parsed is not None:
                self._missing[str(key)] = dt_util.as_utc(parsed)

    async def async_reset(self) -> None:
        """Forget all timers; used when the container list cannot be trusted."""
        async with self._lock:
            await self._async_clear()

    async def async_process(self, present: set[str]) -> set[str]:
        """Return container keys whose registry entries were removed."""
        async with self._lock:
            if self._delay is None:
                await self._async_clear()
                return set()

            entity_registry = er.async_get(self._hass)
            device_registry = dr.async_get(self._hass)
            entry_id = self._entry.entry_id
            environment_device_ids = {
                device.id
                for device in dr.async_entries_for_config_entry(device_registry, entry_id)
                if (DOMAIN, self._environment_id) in device.identifiers
            }

            # Only entities on container devices are candidates. Environment-level
            # sensors such as "cleanup status" also end in "_status".
            prefix = f"{self._environment_id}_"
            by_key: dict[str, list[er.RegistryEntry]] = {}
            for entity in er.async_entries_for_config_entry(entity_registry, entry_id):
                if (
                    entity.device_id is None
                    or entity.device_id in environment_device_ids
                    or not entity.unique_id.startswith(prefix)
                ):
                    continue
                key = container_key(entity.unique_id[len(prefix) :])
                if key:
                    by_key.setdefault(key, []).append(entity)

            missing_now = set(by_key) - present
            now = self._now()
            changed = False
            for key in missing_now:
                if key not in self._missing:
                    self._missing[key] = now
                    changed = True
            for key in list(self._missing):
                if key not in missing_now:
                    del self._missing[key]
                    changed = True

            expired = {
                key for key in missing_now if now - self._missing[key] >= self._delay
            }
            affected_devices: set[str] = set()
            for key in expired:
                for entity in by_key[key]:
                    affected_devices.add(entity.device_id)
                    entity_registry.async_remove(entity.entity_id)
                del self._missing[key]
                changed = True

            if expired:
                self._remove_empty_devices(
                    entity_registry, device_registry, affected_devices
                )
                _LOGGER.info(
                    "Removed Arcane entries for containers missing for %s: %s",
                    self._delay,
                    ", ".join(sorted(expired)),
                )
            if changed:
                await self._async_save()
            return expired

    def _remove_empty_devices(
        self,
        entity_registry: er.EntityRegistry,
        device_registry: dr.DeviceRegistry,
        affected_devices: set[str],
    ) -> None:
        """Remove only affected container devices that no longer have entities."""
        entry_id = self._entry.entry_id
        still_used = {
            entity.device_id
            for entity in er.async_entries_for_config_entry(entity_registry, entry_id)
        }
        for device in dr.async_entries_for_config_entry(device_registry, entry_id):
            if device.id not in affected_devices or device.id in still_used:
                continue
            if set(device.config_entries) <= {entry_id}:
                device_registry.async_remove_device(device.id)
            else:
                device_registry.async_update_device(
                    device.id, remove_config_entry_id=entry_id
                )

    async def _async_clear(self) -> None:
        if self._missing:
            self._missing.clear()
            await self._async_save()

    async def _async_save(self) -> None:
        data: dict[str, Any] = {
            "missing": {key: value.isoformat() for key, value in self._missing.items()}
        }
        await self._store.async_save(data)


async def async_setup_stale_cleanup(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: ArcaneCoordinator
) -> None:
    """Check after every successful poll; failed polls never advance a timer."""
    hours = int(entry.options.get(CONF_STALE_REMOVAL_HOURS, DEFAULT_STALE_REMOVAL_HOURS))
    cleaner = StaleContainerCleaner(hass, entry, coordinator.environment_id, hours)
    await cleaner.async_load()

    async def _run() -> None:
        try:
            containers = coordinator.data.containers if coordinator.data else {}
            if not containers:
                # An empty list may be an outage or an environment being rebuilt;
                # never treat it as proof that every container was deleted.
                await cleaner.async_reset()
                return
            removed = await cleaner.async_process(set(containers))
            if removed:
                coordinator.forget_containers(removed)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Arcane stale-container cleanup failed")

    @callback
    def _schedule() -> None:
        if cleaner.busy:
            return
        entry.async_create_background_task(
            hass, _run(), f"arcane_stale_cleanup_{entry.entry_id}", eager_start=False
        )

    entry.async_on_unload(coordinator.async_add_listener(_schedule))
    _schedule()
