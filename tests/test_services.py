"""Exercise the real action schema/handler with HA admin-registration doubles."""

from enum import Enum
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import voluptuous as vol


class EntryState(Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


def load_services():
    package_name = "_arcane_service_tests"
    package = ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components/arcane")
    ]
    register = Mock()
    attributes = {
        "homeassistant.config_entries": {"ConfigEntryState": EntryState},
        "homeassistant.core": {
            "HomeAssistant": object, "ServiceCall": object,
            "SupportsResponse": SimpleNamespace(OPTIONAL="optional"),
            "callback": lambda func: func,
        },
        "homeassistant.exceptions": {
            "HomeAssistantError": RuntimeError, "ServiceValidationError": ValueError,
        },
        "homeassistant.helpers.config_validation": {
            "string": vol.Coerce(str), "boolean": vol.Boolean(),
        },
        "homeassistant.helpers.service": {"async_register_admin_service": register},
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
        services = importlib.import_module(f"{package_name}.services")
    return services, register


services, register_admin = load_services()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        register_admin.reset_mock()
        self.hass = Mock()
        self.hass.services.has_service.return_value = False
        self.prune = AsyncMock(return_value={"accepted": True, "activity_id": "job"})
        self.entry = SimpleNamespace(
            domain="arcane", state=EntryState.LOADED,
            runtime_data=SimpleNamespace(async_prune=self.prune),
        )
        self.hass.config_entries.async_get_entry.return_value = self.entry
        services.async_register_services(self.hass)
        self.handler = register_admin.call_args.args[3]

    async def call(self, data, response=True):
        data = services.PRUNE_SCHEMA(data)
        return await self.handler(SimpleNamespace(data=data, return_response=response))

    def test_registers_admin_only_action_and_optional_response(self):
        register_admin.assert_called_once()
        self.assertEqual(register_admin.call_args.args[1:3], ("arcane", "prune"))
        self.assertEqual(register_admin.call_args.kwargs["supports_response"], "optional")
        self.prune.assert_not_awaited()

    async def test_default_selections_do_not_delete_anything(self):
        with self.assertRaisesRegex(ValueError, "Select at least one"):
            await self.call({"config_entry_id": "entry", "confirm": True})
        self.prune.assert_not_awaited()

    async def test_missing_or_false_confirmation_never_calls_api(self):
        for confirm in (None, False):
            data = {"config_entry_id": "entry", "images": "dangling"}
            if confirm is not None:
                data["confirm"] = confirm
            with self.subTest(confirm=confirm), self.assertRaises((ValueError, vol.Invalid)):
                await self.call(data)
        self.prune.assert_not_awaited()

    async def test_volume_requires_separate_confirmation(self):
        with self.assertRaisesRegex(ValueError, "Volume cleanup"):
            await self.call({
                "config_entry_id": "entry", "confirm": True, "volumes": "anonymous",
            })
        self.prune.assert_not_awaited()

    async def test_only_selected_resources_are_sent(self):
        result = await self.call({
            "config_entry_id": "entry", "confirm": True, "images": "dangling",
        })
        self.assertEqual(result["activity_id"], "job")
        self.prune.assert_awaited_once_with({"images": {"mode": "dangling"}})

    async def test_all_resources_with_explicit_volume_confirmation(self):
        await self.call({
            "config_entry_id": "entry", "confirm": True, "confirm_volumes": True,
            "containers": "stopped", "images": "all", "networks": "unused",
            "volumes": "all", "build_cache": "all",
        }, response=False)
        self.prune.assert_awaited_once_with({
            "containers": {"mode": "stopped"}, "images": {"mode": "all"},
            "networks": {"mode": "unused"}, "volumes": {"mode": "all"},
            "buildCache": {"mode": "all"},
        })

    async def test_wrong_domain_or_unloaded_entry_is_rejected(self):
        for entry in (
            None,
            SimpleNamespace(domain="other", state=EntryState.LOADED),
            SimpleNamespace(domain="arcane", state=EntryState.NOT_LOADED),
        ):
            self.hass.config_entries.async_get_entry.return_value = entry
            with self.assertRaisesRegex(ValueError, "loaded Arcane"):
                await self.call({
                    "config_entry_id": "entry", "confirm": True, "images": "dangling",
                })
        self.prune.assert_not_awaited()

    def test_unknown_fields_and_modes_are_rejected(self):
        for extra in ({"images": "arbitrary"}, {"arbitrary": True}):
            with self.subTest(extra=extra), self.assertRaises(vol.Invalid):
                services.PRUNE_SCHEMA({"config_entry_id": "entry", "confirm": True, **extra})

    def test_age_does_not_silently_truncate(self):
        for value in (True, 1.5, 0, -1, float("inf")):
            with self.subTest(value=value), self.assertRaises(vol.Invalid):
                services.PRUNE_SCHEMA({
                    "config_entry_id": "entry", "confirm": True,
                    "images": "older_than", "image_age_hours": value,
                })
        data = services.PRUNE_SCHEMA({
            "config_entry_id": "entry", "confirm": True,
            "images": "older_than", "image_age_hours": 24.0,
        })
        self.assertEqual(data["image_age_hours"], 24)
