"""Data coordinator for Arcane."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ArcaneApiClient, ArcaneApiError, ArcaneAuthError
from .const import (
    CONF_ENVIRONMENT_ID,
    CONF_ENVIRONMENT_NAME,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    is_ephemeral_container,
    update_interval,
)
from .security import security_values
from .storage import storage_values

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
    storage: dict[str, int | float] = field(default_factory=dict)
    cleanup_status: str = "idle"
    cleanup_activity_id: str | None = None
    security: dict[str, Any] = field(default_factory=dict)
    security_scan: dict[str, Any] = field(default_factory=lambda: {"status": "idle"})


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
        self._action_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_lock = asyncio.Lock()
        self._security_scan_task: asyncio.Task[None] | None = None
        self._security_scan_lock = asyncio.Lock()
        self._forget_callbacks: list[Callable[[set[str]], None]] = []
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

    def async_add_forget_listener(
        self, callback: Callable[[set[str]], None]
    ) -> Callable[[], None]:
        """Let platforms drop removed containers so a return recreates entities."""
        self._forget_callbacks.append(callback)

        def remove() -> None:
            if callback in self._forget_callbacks:
                self._forget_callbacks.remove(callback)

        return remove

    def forget_containers(self, keys: set[str]) -> None:
        """Tell platforms that registry entries for these containers were removed."""
        for callback in list(self._forget_callbacks):
            callback(set(keys))

    async def _async_update_data(self) -> ArcaneData:
        try:
            summary, containers_list = await asyncio.gather(
                self.client.async_get_update_summary(self.environment_id),
                self.client.async_list_containers(self.environment_id),
            )
        except ArcaneApiError as err:
            raise UpdateFailed(str(err)) from err

        containers_list = [
            container
            for container in containers_list
            if not is_ephemeral_container(container_name(container))
        ]

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
            self.client.async_get_system_stats(self.environment_id),
            self.client.async_get_disk_usage(self.environment_id),
            self.client.async_get_security_summary(self.environment_id),
            self.client.async_get_security_overview(self.environment_id),
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
            storage=storage_values(value(9, {}), value(10, {})),
            cleanup_status=previous.cleanup_status if previous else "idle",
            cleanup_activity_id=previous.cleanup_activity_id if previous else None,
            security=security_values(value(11, {}), value(12, {})),
            security_scan=previous.security_scan if previous else {"status": "idle"},
        )

    @property
    def security_scan_in_progress(self) -> bool:
        """Avoid duplicate bulk submissions while a request is being followed."""
        return (
            self._security_scan_task is not None
            and not self._security_scan_task.done()
        )

    async def async_scan_security(self) -> None:
        """Only a manual button press reaches this scan submission path."""
        async with self._security_scan_lock:
            if self.security_scan_in_progress:
                raise ArcaneApiError("Security scan requests are already being processed")
            if not self.client.uses_api_v3:
                raise ArcaneApiError("Security scans require Arcane 3.0 or newer")
            images = await self.client.async_list_images(self.environment_id)
            if any(not image.get("id") for image in images):
                raise ArcaneApiError("Arcane returned an image without an ID")
            image_ids = list(dict.fromkeys(str(image["id"]) for image in images))
            if not image_ids:
                raise ArcaneApiError("No images are available to scan")
            self.async_set_updated_data(replace(self.data, security_scan={
                "status": "submitting", "total_images": len(image_ids),
                "accepted_images": 0, "failed_images": 0,
            }))
            self._security_scan_task = self.config_entry.async_create_background_task(
                self.hass, self._async_submit_security_scans(image_ids),
                f"arcane_security_scan_{self.environment_id}", eager_start=False,
            )
            self.async_update_listeners()

    async def _async_refresh_security(self) -> None:
        """Refresh stored security readings without polling all other statistics."""
        results = await asyncio.gather(
            self.client.async_get_security_summary(self.environment_id),
            self.client.async_get_security_overview(self.environment_id),
            return_exceptions=True,
        )
        self.async_set_updated_data(replace(
            self.data,
            security=security_values(*(
                result if isinstance(result, dict) else {} for result in results
            )),
        ))

    async def _async_submit_security_scans(self, image_ids: list[str]) -> None:
        """Queue each image once, then refresh results; acceptance is not completion."""
        accepted = failed = 0
        try:
            for index, image_id in enumerate(image_ids):
                try:
                    await self.client.async_scan_image(self.environment_id, image_id)
                except ArcaneAuthError as err:
                    failed += len(image_ids) - index
                    _LOGGER.warning("Security scan requests stopped: %s", err)
                    break
                except ArcaneApiError as err:
                    failed += 1
                    _LOGGER.warning(
                        "Security scan for image %s was not confirmed: %s", image_id, err
                    )
                else:
                    accepted += 1
                self.async_set_updated_data(replace(self.data, security_scan={
                    "status": "submitting", "total_images": len(image_ids),
                    "accepted_images": accepted, "failed_images": failed,
                }))
            status = "submitted" if failed == 0 else ("partial" if accepted else "failed")
            self.async_set_updated_data(replace(self.data, security_scan={
                "status": status, "total_images": len(image_ids),
                "accepted_images": accepted, "failed_images": failed,
            }))
            await self._async_refresh_security()
            if accepted:
                # Read-only follow-ups at 15, 60, 120 and 300 seconds. Do not
                # claim all scans completed just because submissions succeeded.
                for delay in (15, 45, 60, 180):
                    await asyncio.sleep(delay)
                    await self._async_refresh_security()
        finally:
            if self._security_scan_task is asyncio.current_task():
                self._security_scan_task = None
                self.async_update_listeners()

    async def async_prune(self, request: dict[str, Any]) -> dict[str, Any]:
        """Submit only an explicitly selected cleanup and monitor acceptance."""
        async with self._cleanup_lock:
            if self._cleanup_task is not None and not self._cleanup_task.done():
                raise ArcaneApiError("A cleanup is already being monitored")
            result = await self.client.async_prune(self.environment_id, request)
            activity_id = result.get("activityId")
            self.async_set_updated_data(replace(
                self.data, cleanup_status="submitted", cleanup_activity_id=activity_id,
            ))
            self._cleanup_task = self.config_entry.async_create_background_task(
                self.hass, self._async_watch_cleanup(activity_id),
                f"arcane_cleanup_{self.environment_id}", eager_start=False,
            )
            return {"accepted": True, "activity_id": activity_id}

    async def _async_watch_cleanup(self, activity_id: str | None) -> None:
        """Acceptance is not completion; poll progress, never repeat the POST."""
        try:
            status = "unknown"
            if activity_id:
                for _ in range(24):
                    await asyncio.sleep(5)
                    try:
                        activity = await self.client.async_get_activity(
                            self.environment_id, activity_id
                        )
                    except ArcaneApiError as err:
                        _LOGGER.debug("Cleanup progress unavailable: %s", err)
                        continue
                    current_status = activity.get("status")
                    if current_status not in (
                        "queued", "running", "success", "failed", "cancelled"
                    ):
                        continue
                    self.async_set_updated_data(
                        replace(self.data, cleanup_status=current_status)
                    )
                    if current_status in ("success", "failed", "cancelled"):
                        status = current_status
                        break
            self.async_set_updated_data(replace(self.data, cleanup_status=status))
            self.client.invalidate_disk_usage(self.environment_id)
            await self.async_request_refresh()
        finally:
            if self._cleanup_task is asyncio.current_task():
                self._cleanup_task = None

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

    async def async_pause_container(self, key: str) -> None:
        """Pause the container represented by a stable key."""
        container = self.data.containers.get(key) if self.data else None
        if container is None:
            raise ArcaneApiError(f"Container {key} is no longer available")
        await self.client.async_pause_container(
            self.environment_id, str(container["id"])
        )
        self._schedule_action_refresh(key)

    async def async_unpause_container(self, key: str) -> None:
        """Resume the container represented by a stable key."""
        container = self.data.containers.get(key) if self.data else None
        if container is None:
            raise ArcaneApiError(f"Container {key} is no longer available")
        await self.client.async_unpause_container(
            self.environment_id, str(container["id"])
        )
        self._schedule_action_refresh(key)

    async def async_restart_container(self, key: str) -> None:
        """Restart the container represented by a stable key."""
        container = self.data.containers.get(key) if self.data else None
        if container is None:
            raise ArcaneApiError(f"Container {key} is no longer available")
        await self.client.async_restart_container(
            self.environment_id, str(container["id"])
        )
        self._schedule_action_refresh(key)

    async def async_stop_container(self, key: str) -> None:
        """Stop the container represented by a stable key."""
        container = self.data.containers.get(key) if self.data else None
        if container is None:
            raise ArcaneApiError(f"Container {key} is no longer available")
        await self.client.async_stop_container(
            self.environment_id, str(container["id"])
        )
        self._schedule_action_refresh(key)

    async def async_start_container(self, key: str) -> None:
        """Start a stopped container represented by a stable key."""
        container = self.data.containers.get(key) if self.data else None
        if container is None:
            raise ArcaneApiError(f"Container {key} is no longer available")
        await self.client.async_start_container(
            self.environment_id, str(container["id"])
        )
        self._schedule_action_refresh(key)

    def _schedule_action_refresh(self, key: str) -> None:
        """Replace pending checks for this container; HA cancels them on unload."""
        previous = self._action_refresh_tasks.get(key)
        if previous is not None:
            previous.cancel()
        self._action_refresh_tasks[key] = (
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_poll_action_state(key),
                f"arcane_action_status_{self.environment_id}_{key}",
                eager_start=False,
            )
        )

    async def _async_poll_action_state(self, key: str) -> None:
        """Check immediately and at 3/6/9/12/15 seconds, bypassing debounce."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            for offset in (0, 3, 6, 9, 12, 15):
                delay = started + offset - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                container = self.data.containers.get(key) if self.data else None
                if container is None:
                    break
                container_id = str(container["id"])
                try:
                    state = await self.client.async_get_container_state(
                        self.environment_id, container_id
                    )
                except ArcaneApiError as err:
                    _LOGGER.debug("Post-action state check for %s failed: %s", key, err)
                    continue
                # A full poll or redeploy might have replaced the container
                # while the detail request was in flight. Preserve newer data.
                current = self.data
                latest = current.containers.get(key) if current else None
                if latest is None or str(latest["id"]) != container_id:
                    continue
                containers = {
                    **current.containers,
                    key: {**latest, "state": state},
                }
                self.async_set_updated_data(replace(current, containers=containers))
        finally:
            if self._action_refresh_tasks.get(key) is asyncio.current_task():
                self._action_refresh_tasks.pop(key, None)
