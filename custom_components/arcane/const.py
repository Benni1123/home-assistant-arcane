"""Constants for the Arcane integration."""

import re
from datetime import timedelta

DOMAIN = "arcane"
PLATFORMS = ["sensor", "button", "update"]

CONF_API_KEY = "api_key"
CONF_ENVIRONMENT_ID = "environment_id"
CONF_ENVIRONMENT_NAME = "environment_name"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_URL = "url"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_SCAN_INTERVAL = 300
MIN_SCAN_INTERVAL = 30


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
