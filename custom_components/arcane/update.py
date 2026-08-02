"""Update entities for Arcane containers."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneCoordinator
from .entity import ArcaneEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ArcaneCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up an update entity for every discovered container."""
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
            [ArcaneContainerUpdate(coordinator, key) for key in new_keys]
        )

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


def _short_digest(digest: str | None) -> str | None:
    if not digest:
        return None
    return digest.removeprefix("sha256:")[:12]


class ArcaneContainerUpdate(ArcaneEntity, UpdateEntity):
    """Represent the image update state of one Docker container."""

    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_icon = "mdi:docker"

    def __init__(self, coordinator: ArcaneCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = key
        self._attr_unique_id = f"{coordinator.environment_id}_{key}_update"
        self._installing = False

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
    def entity_picture(self) -> str | None:
        """Use Arcane's service icon metadata when available."""
        container = self._container or {}
        icon_url = container.get("iconLightUrl") or container.get("iconDarkUrl")
        if icon_url:
            icon_url = str(icon_url).strip()
            if urlparse(icon_url).scheme in ("http", "https"):
                return icon_url
            if icon_url.startswith("/"):
                return urljoin(f"{self.coordinator.client.base_url}/", icon_url)

        image = str(container.get("image", "")).lower()
        if "getarcaneapp/arcane" in image or self._key.lower() == "arcane":
            return (
                f"{self.coordinator.client.base_url}"
                "/api/app-images/pwa/icon-192x192.png"
            )
        return None

    @property
    def installed_version(self) -> str | None:
        """Return Arcane's current image version or digest."""
        container = self._container
        if container is None:
            return None
        info = container.get("updateInfo") or {}
        current_version = info.get("currentVersion")
        latest_version = info.get("latestVersion")
        if info.get("hasUpdate") and current_version == latest_version:
            current_version = None
        return (
            current_version
            or _short_digest(info.get("currentDigest"))
            or _short_digest(container.get("imageId"))
        )

    @property
    def latest_version(self) -> str | None:
        """Return the newest registry version or digest."""
        container = self._container
        if container is None:
            return None
        info = container.get("updateInfo") or {}
        if not info.get("hasUpdate"):
            return self.installed_version
        latest_version = info.get("latestVersion")
        if latest_version == info.get("currentVersion"):
            latest_version = None
        return (
            latest_version
            or _short_digest(info.get("latestDigest"))
            or self.installed_version
        )

    @property
    def in_progress(self) -> bool:
        """Return whether HA initiated an update for this container."""
        return self._installing

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose useful Arcane image details."""
        container = self._container or {}
        info = container.get("updateInfo") or {}
        return {
            "image": container.get("image"),
            "container_state": container.get("state"),
            "current_digest": info.get("currentDigest"),
            "latest_digest": info.get("latestDigest"),
            "update_type": info.get("updateType"),
            "last_checked": info.get("checkTime"),
            "arcane_error": info.get("error") or None,
        }

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Install the latest image using Arcane's update strategy."""
        self._installing = True
        self.async_write_ha_state()
        try:
            await self.coordinator.async_install_update(self._key)
        finally:
            self._installing = False
            self.async_write_ha_state()
