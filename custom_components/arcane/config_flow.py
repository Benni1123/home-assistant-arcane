"""Config flow for Arcane."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArcaneApiClient, ArcaneApiError, ArcaneAuthError, ArcaneConnectionError
from .const import (
    CONF_ENVIRONMENT_ID,
    CONF_ENVIRONMENT_NAME,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


class ArcaneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an Arcane config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}
        self._environments: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect server credentials and discover environments."""
        errors: dict[str, str] = {}
        if user_input is not None:
            url = ArcaneApiClient.normalize_url(user_input[CONF_URL])
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors["base"] = "invalid_url"
            else:
                client = ArcaneApiClient(
                    async_get_clientsession(self.hass),
                    url,
                    user_input[CONF_API_KEY],
                    user_input[CONF_VERIFY_SSL],
                )
                try:
                    environments = await client.async_list_environments()
                except ArcaneAuthError:
                    errors["base"] = "invalid_auth"
                except ArcaneConnectionError:
                    errors["base"] = "cannot_connect"
                except ArcaneApiError:
                    errors["base"] = "unknown"
                else:
                    enabled = [env for env in environments if env.get("enabled", True)]
                    if not enabled:
                        errors["base"] = "no_environments"
                    else:
                        self._connection_data = {
                            CONF_URL: url,
                            CONF_API_KEY: user_input[CONF_API_KEY],
                            CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                        }
                        self._environments = enabled
                        if len(enabled) == 1:
                            return await self._finish(enabled[0])
                        return await self.async_step_environment()

        schema = vol.Schema(
            {
                vol.Required(CONF_URL): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_VERIFY_SSL, default=True): bool,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_environment(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose one Arcane environment."""
        choices = {
            str(env["id"]): str(env.get("name") or env["id"])
            for env in self._environments
        }
        if user_input is not None:
            selected = next(
                env
                for env in self._environments
                if str(env["id"]) == user_input[CONF_ENVIRONMENT_ID]
            )
            return await self._finish(selected)

        return self.async_show_form(
            step_id="environment",
            data_schema=vol.Schema(
                {vol.Required(CONF_ENVIRONMENT_ID): vol.In(choices)}
            ),
        )

    async def _finish(self, environment: dict[str, Any]) -> FlowResult:
        environment_id = str(environment["id"])
        environment_name = str(environment.get("name") or environment_id)
        await self.async_set_unique_id(
            f"{self._connection_data[CONF_URL]}|{environment_id}"
        )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Arcane – {environment_name}",
            data={
                **self._connection_data,
                CONF_ENVIRONMENT_ID: environment_id,
                CONF_ENVIRONMENT_NAME: environment_name,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ArcaneOptionsFlow:
        """Return the options flow."""
        return ArcaneOptionsFlow(config_entry)


class ArcaneOptionsFlow(config_entries.OptionsFlow):
    """Configure Arcane polling options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage Arcane options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = int(
            self._config_entry.options.get(
                CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)
                    )
                }
            ),
        )
