"""Statistics sensors for Arcane."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ArcaneCoordinator
from .entity import ArcaneEntity


@dataclass(frozen=True, kw_only=True)
class ArcaneSensorEntityDescription(SensorEntityDescription):
    """Describe a statistic returned by Arcane."""

    source: str
    source_key: str


SENSORS: tuple[ArcaneSensorEntityDescription, ...] = (
    ArcaneSensorEntityDescription(
        key="available_updates",
        translation_key="available_updates",
        icon="mdi:package-up",
        native_unit_of_measurement="updates",
        source="summary",
        source_key="imagesWithUpdates",
    ),
    ArcaneSensorEntityDescription(
        key="update_errors",
        translation_key="update_errors",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        source="summary",
        source_key="errorsCount",
    ),
    ArcaneSensorEntityDescription(
        key="digest_updates",
        translation_key="digest_updates",
        icon="mdi:fingerprint",
        entity_category=EntityCategory.DIAGNOSTIC,
        source="summary",
        source_key="digestUpdates",
    ),
    ArcaneSensorEntityDescription(
        key="containers_total",
        translation_key="containers_total",
        icon="mdi:docker",
        state_class=SensorStateClass.MEASUREMENT,
        source="container_counts",
        source_key="totalContainers",
    ),
    ArcaneSensorEntityDescription(
        key="containers_running",
        translation_key="containers_running",
        icon="mdi:play-circle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="container_counts",
        source_key="runningContainers",
    ),
    ArcaneSensorEntityDescription(
        key="containers_stopped",
        translation_key="containers_stopped",
        icon="mdi:stop-circle-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="container_counts",
        source_key="stoppedContainers",
    ),
    ArcaneSensorEntityDescription(
        key="images_total",
        translation_key="images_total",
        icon="mdi:layers-triple-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="image_counts",
        source_key="totalImages",
    ),
    ArcaneSensorEntityDescription(
        key="images_in_use",
        translation_key="images_in_use",
        icon="mdi:layers-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="image_counts",
        source_key="imagesInuse",
    ),
    ArcaneSensorEntityDescription(
        key="images_unused",
        translation_key="images_unused",
        icon="mdi:layers-off-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="image_counts",
        source_key="imagesUnused",
    ),
    ArcaneSensorEntityDescription(
        key="image_storage_size",
        translation_key="image_storage_size",
        icon="mdi:harddisk",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        source="image_counts",
        source_key="totalImageSize",
    ),
    ArcaneSensorEntityDescription(
        key="volumes_total",
        translation_key="volumes_total",
        icon="mdi:database-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="volume_counts",
        source_key="total",
    ),
    ArcaneSensorEntityDescription(
        key="volumes_in_use",
        translation_key="volumes_in_use",
        icon="mdi:database-check-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="volume_counts",
        source_key="inuse",
    ),
    ArcaneSensorEntityDescription(
        key="volumes_unused",
        translation_key="volumes_unused",
        icon="mdi:database-off-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="volume_counts",
        source_key="unused",
    ),
    ArcaneSensorEntityDescription(
        key="networks_total",
        translation_key="networks_total",
        icon="mdi:lan",
        state_class=SensorStateClass.MEASUREMENT,
        source="network_counts",
        source_key="total",
    ),
    ArcaneSensorEntityDescription(
        key="networks_in_use",
        translation_key="networks_in_use",
        icon="mdi:lan-connect",
        state_class=SensorStateClass.MEASUREMENT,
        source="network_counts",
        source_key="inuse",
    ),
    ArcaneSensorEntityDescription(
        key="networks_unused",
        translation_key="networks_unused",
        icon="mdi:lan-disconnect",
        state_class=SensorStateClass.MEASUREMENT,
        source="network_counts",
        source_key="unused",
    ),
    ArcaneSensorEntityDescription(
        key="projects_total",
        translation_key="projects_total",
        icon="mdi:view-dashboard-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="project_counts",
        source_key="totalProjects",
    ),
    ArcaneSensorEntityDescription(
        key="projects_running",
        translation_key="projects_running",
        icon="mdi:view-dashboard-variant-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="project_counts",
        source_key="runningProjects",
    ),
    ArcaneSensorEntityDescription(
        key="projects_stopped",
        translation_key="projects_stopped",
        icon="mdi:view-dashboard-variant",
        state_class=SensorStateClass.MEASUREMENT,
        source="project_counts",
        source_key="stoppedProjects",
    ),
    ArcaneSensorEntityDescription(
        key="projects_archived",
        translation_key="projects_archived",
        icon="mdi:archive-outline",
        state_class=SensorStateClass.MEASUREMENT,
        source="project_counts",
        source_key="archivedProjects",
    ),
    ArcaneSensorEntityDescription(
        key="published_ports",
        translation_key="published_ports",
        icon="mdi:ethernet",
        state_class=SensorStateClass.MEASUREMENT,
        source="root",
        source_key="port_count",
    ),
    ArcaneSensorEntityDescription(
        key="docker_cpus",
        translation_key="docker_cpus",
        icon="mdi:cpu-64-bit",
        entity_category=EntityCategory.DIAGNOSTIC,
        source="docker_info",
        source_key="NCPU",
    ),
    ArcaneSensorEntityDescription(
        key="docker_memory",
        translation_key="docker_memory",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        source="docker_info",
        source_key="MemTotal",
    ),
    ArcaneSensorEntityDescription(
        key="arcane_version",
        translation_key="arcane_version",
        icon="mdi:information-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        source="version",
        source_key="displayVersion",
    ),
    ArcaneSensorEntityDescription(
        key="docker_version",
        translation_key="docker_version",
        icon="mdi:docker",
        entity_category=EntityCategory.DIAGNOSTIC,
        source="docker_info",
        source_key="ServerVersion",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ArcaneCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up all statistics sensors."""
    async_add_entities(
        ArcaneStatisticsSensor(entry.runtime_data, description)
        for description in SENSORS
    )


class ArcaneStatisticsSensor(ArcaneEntity, SensorEntity):
    """Represent one Arcane environment statistic."""

    entity_description: ArcaneSensorEntityDescription

    def __init__(
        self,
        coordinator: ArcaneCoordinator,
        description: ArcaneSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.environment_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the latest statistic."""
        data = self.coordinator.data
        if self.entity_description.source == "root":
            value = getattr(data, self.entity_description.source_key, None)
        else:
            source = getattr(data, self.entity_description.source, {})
            value = source.get(self.entity_description.source_key)

        if value is None and self.entity_description.key == "arcane_version":
            value = data.version.get("currentVersion")
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose useful version and host details without entity spam."""
        if self.entity_description.key == "available_updates":
            summary = self.coordinator.data.summary
            return {
                "total_images": summary.get("totalImages"),
                "digest_updates": summary.get("digestUpdates"),
                "errors": summary.get("errorsCount"),
            }
        if self.entity_description.key == "arcane_version":
            version = self.coordinator.data.version
            return {
                key: version.get(key)
                for key in (
                    "currentVersion",
                    "newestVersion",
                    "updateAvailable",
                    "shortRevision",
                    "goVersion",
                    "nodeVersion",
                    "svelteKitVersion",
                    "buildTime",
                    "releaseUrl",
                )
                if version.get(key) is not None
            }
        if self.entity_description.key == "docker_version":
            info = self.coordinator.data.docker_info
            return {
                key: info.get(key)
                for key in (
                    "Name",
                    "OperatingSystem",
                    "KernelVersion",
                    "Architecture",
                    "Driver",
                    "CgroupDriver",
                    "CgroupVersion",
                    "DockerRootDir",
                )
                if info.get(key) is not None
            }
        return None
