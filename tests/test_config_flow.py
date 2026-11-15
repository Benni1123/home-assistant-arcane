"""Connection-reuse flow regressions with lightweight Home Assistant doubles."""

import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import voluptuous as vol


class FakeAbort(Exception):
    pass


class FakeFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()

    def _async_current_entries(self):
        return self.entries

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if any(getattr(entry, "unique_id", None) == self.unique_id for entry in self.entries):
            raise FakeAbort("already_configured")


def load_flow():
    package_name = "_arcane_flow_tests"
    package = ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components/arcane")
    ]
    attributes = {
        "homeassistant.config_entries": {
            "ConfigFlow": FakeFlow, "OptionsFlow": FakeFlow, "ConfigEntry": object,
        },
        "homeassistant.const": {"CONF_API_KEY": "api_key", "CONF_URL": "url"},
        "homeassistant.core": {"callback": lambda func: func},
        "homeassistant.data_entry_flow": {"FlowResult": dict},
        "homeassistant.helpers.aiohttp_client": {
            "async_get_clientsession": lambda hass: "shared-session",
        },
    }
    modules = {
        package_name: package,
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
    }
    for name, attrs in attributes.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        modules[name] = module
    with patch.dict(sys.modules, modules):
        result = importlib.import_module(f"{package_name}.config_flow")
    return result


config_flow = load_flow()


def entry(entry_id="manager-entry", url="http://manager:3552", env_id="0"):
    return SimpleNamespace(
        entry_id=entry_id, title=f"Arcane – {entry_id}",
        data={
            "url": url, "api_key": "STORED-SECRET",
            "verify_ssl": False, "environment_id": env_id,
            "environment_name": "Local Docker",
        },
        unique_id=f"{url}|{env_id}",
    )


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = config_flow.ArcaneConfigFlow()
        self.flow.hass = object()
        self.flow.entries = []
        self.client = Mock()
        self.client.async_list_environments = AsyncMock(return_value=[
            {"id": "0", "name": "Local Docker"},
            {"id": "agent-1", "name": "Server"},
        ])
        self.client.async_get_update_summary = AsyncMock(return_value={})
        self.factory = Mock(return_value=self.client)
        self.factory.normalize_url = config_flow.ArcaneApiClient.normalize_url
        self.patcher = patch.object(config_flow, "ArcaneApiClient", self.factory)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def reuse(self, selected="manager-entry"):
        return await self.flow.async_step_reuse_connection({
            "connection_entry_id": selected,
        })

    async def test_first_setup_retains_manual_credentials_form(self):
        result = await self.flow.async_step_user()
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "user")
        self.assertIn("api_key", {str(key) for key in result["data_schema"].schema})
        self.factory.assert_not_called()

    async def test_existing_entry_offers_reuse_or_new_manager_without_secret(self):
        self.flow.entries = [entry()]
        result = await self.flow.async_step_user()
        self.assertEqual(result["type"], "menu")
        self.assertEqual(result["menu_options"], ["reuse_connection", "new_connection"])
        self.assertNotIn("STORED-SECRET", repr(result))
        self.factory.assert_not_called()

    async def test_reuse_selection_never_exposes_key_or_prefills_credentials(self):
        self.flow.entries = [entry()]
        result = await self.flow.async_step_reuse_connection()
        self.assertEqual(
            {str(key) for key in result["data_schema"].schema}, {"connection_entry_id"}
        )
        self.assertNotIn("STORED-SECRET", repr(result))
        self.factory.assert_not_called()

    async def test_saved_connection_and_tls_are_reused_and_agents_reloaded(self):
        source = entry(url="http://manager:3552/api/")
        before = dict(source.data)
        self.flow.entries = [source]
        result = await self.reuse()
        self.factory.assert_called_once_with(
            "shared-session", "http://manager:3552", "STORED-SECRET", False
        )
        self.client.async_list_environments.assert_awaited_once()
        self.assertEqual(result["step_id"], "environment")
        self.assertNotIn("STORED-SECRET", repr(result))
        schema = result["data_schema"]
        self.assertEqual(schema({"environment_id": "agent-1"}), {"environment_id": "agent-1"})
        with self.assertRaises(vol.Invalid):
            schema({"environment_id": "0"})
        created = await self.flow.async_step_environment({"environment_id": "agent-1"})
        self.assertEqual(created["type"], "create_entry")
        self.assertEqual(created["data"]["api_key"], "STORED-SECRET")
        self.assertEqual(created["data"]["environment_id"], "agent-1")
        self.assertEqual(self.flow.unique_id, "http://manager:3552|agent-1")
        self.client.async_get_update_summary.assert_awaited_once_with("agent-1")
        self.assertEqual(source.data, before)

    async def test_tls_defaults_to_verification_if_older_entry_has_no_setting(self):
        source = entry()
        source.data.pop("verify_ssl")
        self.flow.entries = [source]
        await self.reuse()
        self.assertIs(self.factory.call_args.args[3], True)

    async def test_already_configured_agents_are_filtered_for_same_manager_only(self):
        self.flow.entries = [
            entry(),
            entry("other-manager", "http://other:3552", "agent-1"),
        ]
        result = await self.reuse()
        result["data_schema"]({"environment_id": "agent-1"})
        self.flow.entries.append(entry("already-added", env_id="agent-1"))
        result = await self.flow.async_step_environment()
        self.assertEqual(result, {"type": "abort", "reason": "no_new_environments"})

    async def test_disabled_agents_and_missing_ids_are_not_offered(self):
        self.flow.entries = [entry()]
        self.client.async_list_environments.return_value = [
            {"id": "0"}, {"id": "disabled", "enabled": False}, {"name": "No ID"},
        ]
        result = await self.reuse()
        self.assertEqual(result["reason"], "no_new_environments")

    async def test_empty_visible_list_reports_no_environments(self):
        self.flow.entries = [entry()]
        self.client.async_list_environments.return_value = []
        result = await self.reuse()
        self.assertEqual(result["step_id"], "reuse_connection")
        self.assertEqual(result["errors"]["base"], "no_environments")

    async def test_missing_or_removed_connection_is_handled(self):
        result = await self.flow.async_step_reuse_connection()
        self.assertEqual(result["reason"], "no_existing_connections")
        self.flow.entries = [entry()]
        result = await self.reuse("deleted")
        self.assertEqual(result["errors"]["base"], "invalid_connection")
        self.factory.assert_not_called()

    async def test_stored_key_failures_do_not_leak_credentials(self):
        self.flow.entries = [entry()]
        for error, expected in (
            (config_flow.ArcaneAuthError("denied"), "invalid_auth"),
            (config_flow.ArcaneConnectionError("offline"), "cannot_connect"),
            (config_flow.ArcaneApiError("unexpected"), "unknown"),
        ):
            with self.subTest(expected=expected):
                self.client.async_list_environments.side_effect = error
                result = await self.reuse()
                self.assertEqual(result["errors"]["base"], expected)
                self.assertNotIn("STORED-SECRET", repr(result))

    async def test_unreachable_agent_is_not_created(self):
        self.flow.entries = [entry()]
        await self.reuse()
        self.client.async_get_update_summary.side_effect = config_flow.ArcaneApiError(
            "Agent offline"
        )
        result = await self.flow.async_step_environment({"environment_id": "agent-1"})
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "environment_unreachable")

    async def test_invalid_agent_selection_does_not_crash_or_probe_it(self):
        self.flow.entries = [entry()]
        await self.reuse()
        result = await self.flow.async_step_environment({"environment_id": "arbitrary"})
        self.assertEqual(result["errors"]["base"], "invalid_environment")
        self.client.async_get_update_summary.assert_not_awaited()

    async def test_new_manager_manual_path_still_works(self):
        self.flow.entries = [entry()]
        result = await self.flow.async_step_new_connection()
        self.assertEqual(result["step_id"], "new_connection")
        self.client.async_list_environments.return_value = [{"id": "0", "name": "Other"}]
        result = await self.flow.async_step_new_connection({
            "url": "http://other:3552/", "api_key": "NEW-KEY", "verify_ssl": True,
        })
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["url"], "http://other:3552")
        self.assertEqual(self.flow.unique_id, "http://other:3552|0")

    async def test_manual_invalid_url_stays_in_appropriate_form(self):
        result = await self.flow.async_step_new_connection({
            "url": "manager", "api_key": "NEW-KEY", "verify_ssl": True,
        })
        self.assertEqual(result["step_id"], "new_connection")
        self.assertEqual(result["errors"]["base"], "invalid_url")
        self.factory.assert_not_called()

    async def test_duplicate_unique_id_guard_is_retained(self):
        source = entry()
        self.flow.entries = [source]
        self.flow._connection_data = dict(source.data)
        with self.assertRaisesRegex(FakeAbort, "already_configured"):
            await self.flow._finish({"id": "0", "name": "Local"})


class OptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def form(self, options):
        flow = config_flow.ArcaneOptionsFlow(SimpleNamespace(options=options))
        return flow, await flow.async_step_init()

    async def test_defaults_poll_every_five_minutes_and_remove_after_one_day(self):
        _, result = await self.form({})
        self.assertEqual(
            result["data_schema"]({}),
            {"scan_interval": 300, "stale_removal_hours": 24},
        )

    async def test_existing_polling_option_keeps_its_value_and_gets_the_new_default(self):
        _, result = await self.form({"scan_interval": 120})
        self.assertEqual(
            result["data_schema"]({}),
            {"scan_interval": 120, "stale_removal_hours": 24},
        )

    async def test_zero_disables_removal_and_limits_are_enforced(self):
        _, result = await self.form({})
        schema = result["data_schema"]
        self.assertEqual(
            schema({"scan_interval": 60, "stale_removal_hours": 0})["stale_removal_hours"], 0
        )
        for hours in (-1, 8761, "soon"):
            with self.subTest(hours=hours), self.assertRaises(vol.Invalid):
                schema({"scan_interval": 60, "stale_removal_hours": hours})
        with self.assertRaises(vol.Invalid):
            schema({"scan_interval": 10, "stale_removal_hours": 24})

    async def test_submitted_options_are_saved(self):
        flow, _ = await self.form({})
        result = await flow.async_step_init(
            {"scan_interval": 90, "stale_removal_hours": 48}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(
            result["data"], {"scan_interval": 90, "stale_removal_hours": 48}
        )
