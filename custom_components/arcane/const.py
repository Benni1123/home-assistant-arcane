"""Constants for the Arcane integration."""

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
