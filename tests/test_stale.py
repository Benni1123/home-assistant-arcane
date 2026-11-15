"""Stale-container cleanup with in-memory registries instead of Home Assistant."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ENTRY_ID = "entry-1"
START = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)


class FakeEntityRegistry:
    def __init__(self):
        self.entities = []
        self.removed = []

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entities = [e for e in self.entities if e.entity_id != entity_id]


class FakeDeviceRegistry:
    def __init__(self):
        self.devices = []
        self.removed = []
        self.detached = []

    def async_remove_device(self, device_id):
        self.removed.append(device_id)
        self.devices = [d for d in self.devices if d.id != device_id]

    def async_update_device(self, device_id, remove_config_entry_id):
        self.detached.append((device_id, remove_config_entry_id))
        self.devices = [d for d in self.devices if d.id != device_id]


class FakeStore:
    saved = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return self.saved.get(self.key)

    async def async_save(self, data):
        self.saved[self.key] = data


def load_stale(entity_registry, device_registry):
    package_name = "_arcane_stale_tests"
    package = ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components/arcane")
    ]
    er = ModuleType("homeassistant.helpers.entity_registry")
    er.async_get = lambda hass: entity_registry
    er.async_entries_for_config_entry = lambda registry, entry_id: [
        entity for entity in registry.entities if entity.config_entry_id == entry_id
    ]
    dr = ModuleType("homeassistant.helpers.device_registry")
    dr.async_entries_for_config_entry = lambda registry, entry_id: [
        device for device in registry.devices if entry_id in device.config_entries
    ]
    dr.async_get = lambda hass: device_registry
    helpers = ModuleType("homeassistant.helpers")
    helpers.entity_registry = er
    helpers.device_registry = dr
    storage = ModuleType("homeassistant.helpers.storage")
    storage.Store = FakeStore
    dt = ModuleType("homeassistant.util.dt")
    dt.utcnow = lambda: START
    dt.parse_datetime = datetime.fromisoformat
    dt.as_utc = lambda value: value.astimezone(timezone.utc)
    util = ModuleType("homeassistant.util")
    util.dt = dt
    config_entries = ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object
    core.callback = lambda func: func
    modules = {
        package_name: package,
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.helpers": helpers,
        "homeassistant.helpers.entity_registry": er,
        "homeassistant.helpers.device_registry": dr,
        "homeassistant.helpers.storage": storage,
        "homeassistant.util": util,
        "homeassistant.util.dt": dt,
        "homeassistant.config_entries": config_entries,
        "homeassistant.core": core,
    }
    with patch.dict(sys.modules, modules):
        return importlib.import_module(f"{package_name}.stale")


def entity(entity_id, unique_id, device_id, entry_id=ENTRY_ID):
    return SimpleNamespace(
        entity_id=entity_id, unique_id=unique_id,
        device_id=device_id, config_entry_id=entry_id,
    )


def device(device_id, identifier, config_entries=(ENTRY_ID,)):
    return SimpleNamespace(
        id=device_id, identifiers={("arcane", identifier)},
        config_entries=set(config_entries),
    )


class StaleCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeStore.saved = {}
        self.entities = FakeEntityRegistry()
        self.devices = FakeDeviceRegistry()
        self.stale = load_stale(self.entities, self.devices)
        self.entry = SimpleNamespace(entry_id=ENTRY_ID)
        self.now = START
        # Environment device and entities, including a "_status" environment sensor.
        self.devices.devices = [
            device("env", "0"),
            device("firefox", "0_firefox-sync"),
            device("stack", "0_stack"),
            device("other-entry", "9_other", ("other",)),
        ]
        self.entities.entities = [
            entity("sensor.cleanup", "0_cleanup_status", "env"),
            entity("sensor.containers", "0_containers_total", "env"),
            entity("sensor.fs", "0_firefox-sync_status", "firefox"),
            entity("switch.fs", "0_firefox-sync_power", "firefox"),
            entity("select.fs", "0_firefox-sync_control", "firefox"),
            entity("update.fs", "0_firefox-sync_update", "firefox"),
            entity("sensor.web", "0_web_status", "stack"),
            entity("sensor.db", "0_db_status", "stack"),
            entity("sensor.foreign", "0_foreign_status", "other-entry", "other"),
        ]

    def cleaner(self, hours=24):
        cleaner = self.stale.StaleContainerCleaner(object(), self.entry, "0", hours)
        cleaner._now = lambda: self.now
        return cleaner

    async def test_timer_starts_on_first_observation_and_waits_a_day(self):
        cleaner = self.cleaner()
        present = {"web", "db"}
        self.assertEqual(await cleaner.async_process(present), set())
        self.assertEqual(self.entities.removed, [])
        self.now += timedelta(hours=23, minutes=59)
        self.assertEqual(await cleaner.async_process(present), set())
        self.assertEqual(self.entities.removed, [])
        self.assertIn("firefox-sync", FakeStore.saved[f"arcane_stale_{ENTRY_ID}"]["missing"])

    async def test_removes_only_entities_and_device_of_container_missing_for_a_day(self):
        cleaner = self.cleaner()
        present = {"web", "db"}
        await cleaner.async_process(present)
        self.now += timedelta(days=1)
        self.assertEqual(await cleaner.async_process(present), {"firefox-sync"})
        self.assertCountEqual(
            self.entities.removed,
            ["sensor.fs", "switch.fs", "select.fs", "update.fs"],
        )
        self.assertEqual(self.devices.removed, ["firefox"])
        remaining = {e.entity_id for e in self.entities.entities}
        self.assertTrue({"sensor.cleanup", "sensor.containers", "sensor.web",
                         "sensor.db", "sensor.foreign"} <= remaining)
        self.assertEqual({d.id for d in self.devices.devices}, {"env", "stack", "other-entry"})
        self.assertNotIn("firefox-sync", FakeStore.saved[f"arcane_stale_{ENTRY_ID}"]["missing"])

    async def test_environment_level_status_sensors_are_never_container_candidates(self):
        cleaner = self.cleaner()
        present = {"firefox-sync", "web", "db"}
        await cleaner.async_process(present)
        self.now += timedelta(days=30)
        self.assertEqual(await cleaner.async_process(present), set())
        self.assertEqual(self.entities.removed, [])
        self.assertEqual(self.devices.removed, [])

    async def test_one_vanished_service_keeps_the_shared_project_device(self):
        cleaner = self.cleaner()
        present = {"firefox-sync", "web"}
        await cleaner.async_process(present)
        self.now += timedelta(days=2)
        self.assertEqual(await cleaner.async_process(present), {"db"})
        self.assertEqual(self.entities.removed, ["sensor.db"])
        self.assertEqual(self.devices.removed, [])
        self.assertIn("stack", {d.id for d in self.devices.devices})

    async def test_returning_container_resets_its_timer(self):
        cleaner = self.cleaner()
        await cleaner.async_process({"web", "db"})
        self.now += timedelta(hours=20)
        await cleaner.async_process({"web", "db", "firefox-sync"})
        self.now += timedelta(hours=20)
        self.assertEqual(await cleaner.async_process({"web", "db"}), set())
        self.now += timedelta(hours=23)
        self.assertEqual(await cleaner.async_process({"web", "db"}), set())
        self.assertEqual(self.entities.removed, [])

    async def test_timer_survives_restart(self):
        await self.cleaner().async_process({"web", "db"})
        restarted = self.cleaner()
        await restarted.async_load()
        self.now += timedelta(hours=24)
        self.assertEqual(await restarted.async_process({"web", "db"}), {"firefox-sync"})

    async def test_downtime_before_first_observation_is_not_counted(self):
        cleaner = self.cleaner()
        await cleaner.async_load()
        self.assertEqual(await cleaner.async_process({"web", "db"}), set())
        self.assertEqual(self.entities.removed, [])

    async def test_invalid_stored_data_is_ignored(self):
        FakeStore.saved[f"arcane_stale_{ENTRY_ID}"] = {
            "missing": {"firefox-sync": "not a time", "web": 123}
        }
        cleaner = self.cleaner()
        await cleaner.async_load()
        self.assertEqual(await cleaner.async_process({"web", "db"}), set())

    async def test_zero_hours_disables_removal_and_forgets_timers(self):
        enabled = self.cleaner()
        await enabled.async_process({"web", "db"})
        disabled = self.cleaner(0)
        await disabled.async_load()
        self.now += timedelta(days=10)
        self.assertEqual(await disabled.async_process({"web", "db"}), set())
        self.assertEqual(self.entities.removed, [])
        self.assertEqual(FakeStore.saved[f"arcane_stale_{ENTRY_ID}"]["missing"], {})

    async def test_reset_clears_timers(self):
        cleaner = self.cleaner()
        await cleaner.async_process({"web", "db"})
        await cleaner.async_reset()
        self.now += timedelta(days=2)
        self.assertEqual(await cleaner.async_process({"web", "db"}), set())

    async def test_device_shared_with_another_entry_is_detached_not_deleted(self):
        self.devices.devices[1].config_entries.add("other")
        cleaner = self.cleaner()
        await cleaner.async_process({"web", "db"})
        self.now += timedelta(days=1)
        await cleaner.async_process({"web", "db"})
        self.assertEqual(self.devices.removed, [])
        self.assertEqual(self.devices.detached, [("firefox", ENTRY_ID)])

    async def test_empty_container_list_resets_instead_of_removing_everything(self):
        calls = {}
        removed = []

        class Coordinator:
            environment_id = "0"
            data = SimpleNamespace(containers={})
            forget_containers = staticmethod(removed.append)

            def async_add_listener(self, callback):
                calls["listener"] = callback
                return lambda: None

        tasks = []
        entry = SimpleNamespace(
            entry_id=ENTRY_ID, options={},
            async_on_unload=lambda unload: None,
            async_create_background_task=lambda hass, coro, name, eager_start=True:
                tasks.append(asyncio.ensure_future(coro)),
        )
        coordinator = Coordinator()
        await self.stale.async_setup_stale_cleanup(object(), entry, coordinator)
        await asyncio.gather(*tasks)
        self.assertEqual(self.entities.removed, [])
        self.assertEqual(removed, [])

        coordinator.data = SimpleNamespace(containers={"web": {}, "db": {}})
        calls["listener"]()
        await asyncio.gather(*tasks)
        self.assertEqual(removed, [])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(self.entities.removed, [])

    async def test_removal_notifies_platforms_to_recreate_entities_later(self):
        forgotten = []
        listeners = {}

        class Coordinator:
            environment_id = "0"
            data = SimpleNamespace(containers={"web": {}, "db": {}})
            forget_containers = staticmethod(forgotten.append)

            def async_add_listener(self, callback):
                listeners["poll"] = callback
                return lambda: None

        tasks = []
        entry = SimpleNamespace(
            entry_id=ENTRY_ID, options={},
            async_on_unload=lambda unload: None,
            async_create_background_task=lambda hass, coro, name, eager_start=True:
                tasks.append(asyncio.ensure_future(coro)),
        )
        with patch.object(self.stale.dt_util, "utcnow", lambda: self.now):
            await self.stale.async_setup_stale_cleanup(object(), entry, Coordinator())
        await asyncio.gather(*tasks)
        self.assertEqual(forgotten, [])

        self.now += timedelta(days=1)
        listeners["poll"]()
        await asyncio.gather(*tasks)
        self.assertEqual(forgotten, [{"firefox-sync"}])
        self.assertEqual(self.devices.removed, ["firefox"])

    async def test_failures_in_cleanup_do_not_escape_the_background_task(self):
        forgotten = []
        listeners = {}

        class Coordinator:
            environment_id = "0"
            data = SimpleNamespace(containers={"web": {}, "db": {}})
            forget_containers = staticmethod(forgotten.append)

            def async_add_listener(self, callback):
                listeners["poll"] = callback
                return lambda: None

        tasks = []
        entry = SimpleNamespace(
            entry_id=ENTRY_ID, options={},
            async_on_unload=lambda unload: None,
            async_create_background_task=lambda hass, coro, name, eager_start=True:
                tasks.append(asyncio.ensure_future(coro)),
        )
        with patch.object(self.stale.dt_util, "utcnow", lambda: self.now):
            await self.stale.async_setup_stale_cleanup(object(), entry, Coordinator())
        await asyncio.gather(*tasks)

        self.entities.async_remove = Mock(side_effect=RuntimeError("registry busy"))
        self.now += timedelta(days=1)
        with self.assertLogs(self.stale.__name__, level="ERROR"):
            listeners["poll"]()
            await asyncio.gather(*tasks)
        self.assertEqual(forgotten, [])


if __name__ == "__main__":
    unittest.main()
