"""Admin-only cleanup action with explicit resource and data-loss confirmation."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api import ArcaneApiError
from .const import DOMAIN
from .storage import AGE_FIELDS, PRUNE_MODES, build_prune_request


def _whole_hours(value: object) -> int:
    """Allow integral selector numbers, never silently truncate an age."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 1 <= value <= 87600
        or int(value) != value
    ):
        raise vol.Invalid("Age must be a whole number between 1 and 87600 hours")
    return int(value)


PRUNE_SCHEMA = vol.Schema({
    vol.Required("config_entry_id"): cv.string,
    vol.Required("confirm"): cv.boolean,
    vol.Optional("confirm_volumes", default=False): cv.boolean,
    **{
        vol.Optional(resource, default="none"): vol.In(modes)
        for resource, modes in PRUNE_MODES.items()
    },
    **{
        vol.Optional(field): _whole_hours
        for field in AGE_FIELDS.values()
    },
})


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Expose one visual action form; no destructive per-resource buttons."""
    if hass.services.has_service(DOMAIN, "prune"):
        return

    async def async_handle_prune(call: ServiceCall) -> dict | None:
        try:
            request = build_prune_request(call.data)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError("Select a loaded Arcane integration entry")
        try:
            result = await entry.runtime_data.async_prune(request)
        except ArcaneApiError as err:
            raise HomeAssistantError(str(err)) from err
        return result if call.return_response else None

    async_register_admin_service(
        hass, DOMAIN, "prune", async_handle_prune,
        schema=PRUNE_SCHEMA, supports_response=SupportsResponse.OPTIONAL,
    )
