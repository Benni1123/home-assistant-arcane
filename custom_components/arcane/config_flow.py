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
        self._client: ArcaneApiClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Offer stored connections without displaying their credentials."""
        if user_input is not None or not self._reusable_entries():
            return await self._async_connection_form("user", user_input)
        return self.async_show_menu(
            step_id="user",
            menu_options=["reuse_connection", "new_connection"],
        )

    async def async_step_new_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Retain the manual manager/API-key setup path."""
        return await self._async_connection_form("new_connection", user_input)

    def _reusable_entries(self) -> list[config_entries.ConfigEntry]:
        """Only Arcane entries with stored manager credentials can be reused."""
        return [
            entry for entry in self._async_current_entries()
            if entry.data.get(CONF_URL) and entry.data.get(CONF_API_KEY)
        ]

    def _available_environments(self) -> list[dict[str, Any]]:
        """Exclude existing environments on this manager, not on other managers."""
        manager_url = self._connection_data[CONF_URL]
        configured = {
            str(entry.data[CONF_ENVIRONMENT_ID])
            for entry in self._async_current_entries()
            if CONF_ENVIRONMENT_ID in entry.data
            and ArcaneApiClient.normalize_url(entry.data.get(CONF_URL, "")) == manager_url
        }
        return [
            env for env in self._environments if str(env["id"]) not in configured
        ]

    async def async_step_reuse_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Discover additional agents with a selected entry's stored credentials."""
        entries = self._reusable_entries()
        if not entries:
            return self.async_abort(reason="no_existing_connections")
        choices = {entry.entry_id: entry.title for entry in entries}
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = next(
                (entry for entry in entries
                 if entry.entry_id == user_input.get("connection_entry_id")),
                None,
            )
            if selected is None:
                errors["base"] = "invalid_connection"
            else:
                # Copy only connection settings. Never pass the stored key into
                # a form schema, description placeholder, or log message.
                self._connection_data = {
                    CONF_URL: ArcaneApiClient.normalize_url(selected.data[CONF_URL]),
                    CONF_API_KEY: selected.data[CONF_API_KEY],
                    CONF_VERIFY_SSL: selected.data.get(CONF_VERIFY_SSL, True),
                }
                client = ArcaneApiClient(
                    async_get_clientsession(self.hass),
                    self._connection_data[CONF_URL],
                    self._connection_data[CONF_API_KEY],
                    self._connection_data[CONF_VERIFY_SSL],
                )
                error = await self._async_discover_environments(client)
                if error is not None:
                    errors["base"] = error
                else:
                    # Always let the user confirm the new environment, even
                    # when only a single not-yet-configured agent is visible.
                    return await self.async_step_environment()
        return self.async_show_form(
            step_id="reuse_connection",
            data_schema=vol.Schema({
                vol.Required("connection_entry_id"): vol.In(choices),
            }),
            errors=errors,
        )

    async def _async_discover_environments(self, client: ArcaneApiClient) -> str | None:
        """Always reload the manager's current agent list, never a cached HA list."""
        try:
            environments = await client.async_list_environments()
        except ArcaneAuthError:
            return "invalid_auth"
        except ArcaneConnectionError:
            return "cannot_connect"
        except ArcaneApiError:
            return "unknown"
        self._environments = [
            env for env in environments
            if env.get("enabled", True) and env.get("id") is not None
        ]
        self._client = client
        return None if self._environments else "no_environments"

    async def _async_connection_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> FlowResult:
        """Collect server credentials and discover environments."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"error": ""}
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
                self._connection_data = {
                    CONF_URL: url,
                    CONF_API_KEY: user_input[CONF_API_KEY],
                    CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                }
                error = await self._async_discover_environments(client)
                if error is not None:
                    errors["base"] = error
                else:
                    available = self._available_environments()
                    if not available:
                        return self.async_abort(reason="no_new_environments")
                    if len(available) == 1:
                        error = await self._validate_environment(available[0])
                        if error is None:
                            return await self._finish(available[0])
                        errors["base"] = "environment_unreachable"
                        placeholders["error"] = error
                    else:
                        return await self.async_step_environment()

        schema = vol.Schema(
            {
                vol.Required(CONF_URL): str,
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_VERIFY_SSL, default=True): bool,
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_environment(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the user choose one Arcane environment."""
        available = self._available_environments()
        if not available:
            return self.async_abort(reason="no_new_environments")
        choices = {
            str(env["id"]): str(env.get("name") or env["id"])
            for env in available
        }
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"error": ""}
        if user_input is not None:
            selected = next(
                (env for env in available
                 if str(env["id"]) == user_input.get(CONF_ENVIRONMENT_ID)),
                None,
            )
            if selected is None:
                errors["base"] = "invalid_environment"
            else:
                error = await self._validate_environment(selected)
                if error is None:
                    return await self._finish(selected)
                errors["base"] = "environment_unreachable"
                placeholders["error"] = error

        return self.async_show_form(
            step_id="environment",
            data_schema=vol.Schema(
                {vol.Required(CONF_ENVIRONMENT_ID): vol.In(choices)}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _validate_environment(self, environment: dict[str, Any]) -> str | None:
        """Return Arcane's error message if the environment cannot be queried."""
        if self._client is None:
            return None
        try:
            await self._client.async_get_update_summary(str(environment["id"]))
        except ArcaneAuthError:
            return "API key rejected for this environment"
        except ArcaneApiError as err:
            return str(err)
        return None

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
