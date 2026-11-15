"""Storage calculations and explicit, fail-closed cleanup selections."""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

PRUNE_MODES = {
    "containers": ("none", "stopped", "older_than"),
    "images": ("none", "dangling", "all", "older_than"),
    "networks": ("none", "unused", "older_than"),
    "volumes": ("none", "anonymous", "all"),
    "build_cache": ("none", "unused", "all", "older_than"),
}
AGE_FIELDS = {
    "containers": "container_age_hours",
    "images": "image_age_hours",
    "networks": "network_age_hours",
    "build_cache": "build_cache_age_hours",
}


def build_prune_request(options: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Never infer destructive selections, or confirmation, from defaults."""
    if options.get("confirm") is not True:
        raise ValueError("Confirm permanent deletion before running cleanup")
    request: dict[str, dict[str, str]] = {}
    for resource, modes in PRUNE_MODES.items():
        mode = options.get(resource, "none")
        if mode not in modes:
            raise ValueError(f"Invalid cleanup mode for {resource}")
        if mode == "none":
            continue
        if resource == "volumes" and options.get("confirm_volumes") is not True:
            raise ValueError(
                "Volume cleanup needs separate confirmation: data is deleted permanently"
            )
        selection = {"mode": "olderThan" if mode == "older_than" else mode}
        if mode == "older_than":
            hours = options.get(AGE_FIELDS[resource])
            if type(hours) is not int or not 1 <= hours <= 87600:
                raise ValueError(f"Set {AGE_FIELDS[resource]} to 1–87600 hours")
            selection["until"] = f"{hours}h"
        request["buildCache" if resource == "build_cache" else resource] = selection
    if not request:
        raise ValueError("Select at least one resource to clean up")
    return request


def _nonnegative_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def storage_values(
    system_stats: Mapping[str, Any], disk_usage: Mapping[str, Any]
) -> dict[str, int | float]:
    """Keep missing readings unknown; do not report failed reads as zero."""
    result: dict[str, int | float] = {}
    total, used = system_stats.get("diskTotal"), system_stats.get("diskUsage")
    if _nonnegative_number(total) and total > 0:
        result["disk_total"] = total
        if _nonnegative_number(used) and used <= total:
            result.update(
                disk_used=used,
                disk_free=total - used,
                disk_percent=round(used / total * 100, 1),
            )
    reclaimable_keys = (
        "unusedImagesBytes", "buildCacheBytes",
        "unusedVolumesBytes", "stoppedContainersBytes",
    )
    for key in reclaimable_keys:
        value = disk_usage.get(key)
        if _nonnegative_number(value):
            result[key] = value
    if all(key in result for key in reclaimable_keys):
        result["reclaimable_total"] = sum(result[key] for key in reclaimable_keys)
    return result
