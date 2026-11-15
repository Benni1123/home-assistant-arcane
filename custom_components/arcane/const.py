"""Constants for the Arcane integration."""

import re
from datetime import timedelta

DOMAIN = "arcane"
PLATFORMS = ["sensor", "button", "update", "switch", "select"]

CONF_API_KEY = "api_key"
CONF_ENVIRONMENT_ID = "environment_id"
CONF_ENVIRONMENT_NAME = "environment_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_URL = "url"
CONF_VERIFY_SSL = "verify_ssl"

CONF_STALE_REMOVAL_HOURS = "stale_removal_hours"

DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 30
DEFAULT_STALE_REMOVAL_HOURS = 24
MAX_STALE_REMOVAL_HOURS = 8760

# unique_id suffixes of per-container entities: <environment>_<container>_<suffix>
CONTAINER_ENTITY_SUFFIXES = (
    "update", "status", "start", "pause", "resume", "restart", "stop",
    "power", "control",
)


def container_key(rest: str) -> str | None:
    """Strip a per-container entity suffix and return the container key."""
    for suffix in CONTAINER_ENTITY_SUFFIXES:
        if rest.endswith(f"_{suffix}"):
            return rest[: -len(suffix) - 1]
    return None


def update_interval(seconds: int) -> timedelta:
    """Return a validated coordinator update interval."""
    return timedelta(seconds=max(MIN_SCAN_INTERVAL, seconds))


# Short-lived helper containers that must not become HA entities.
# Arcane's self-updater starts a new "arcane-upgrader-<timestamp>" container
# on every run; tracking them leaves one orphaned update entity per run.
EPHEMERAL_CONTAINER_PATTERNS = (
    re.compile(r"^arcane[-_]upgrader([-_]\d+)?$", re.IGNORECASE),
)


def is_ephemeral_container(name: str) -> bool:
    """Return whether a container name belongs to a short-lived helper."""
    return any(pattern.match(name) for pattern in EPHEMERAL_CONTAINER_PATTERNS)
