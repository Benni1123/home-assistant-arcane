"""Lifecycle behavior using minimal HA doubles, without a running HA instance."""

import asyncio
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class FakeEntry:
    def __init__(self):
        self.data = {"environment_id": "0", "environment_name": "Local"}
        self.options = {}
        self.tasks = []

    def async_create_background_task(self, hass, target, name, eager_start=True):
        task = asyncio.create_task(target, name=name)
        self.tasks.append(task)
        return task

    @classmethod
    def __class_getitem__(cls, item):
        return cls


class FakeCoordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, **kwargs):
        self.hass = hass
        self.config_entry = kwargs["config_entry"]
        self.data = None
        self.snapshots = []

    def async_set_updated_data(self, data):
        self.data = data
        self.snapshots.append(data)


class FakeEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return True


def load_modules():
    """Import the real action code while replacing only its HA dependencies."""
    package_name = "_arcane_action_tests"
    package = ModuleType(package_name)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components/arcane")
    ]
    modules = {
        package_name: package,
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
        "homeassistant.components": ModuleType("homeassistant.components"),
    }
    attributes = {
        "homeassistant.config_entries": {"ConfigEntry": FakeEntry},
        "homeassistant.core": {
            "HomeAssistant": object, "callback": lambda func: func,
        },
        "homeassistant.helpers.update_coordinator": {
            "DataUpdateCoordinator": FakeCoordinator, "UpdateFailed": RuntimeError,
        },
        "homeassistant.helpers.entity_platform": {
            "AddConfigEntryEntitiesCallback": object,
        },
        "homeassistant.components.button": {
            "ButtonEntity": type("ButtonEntity", (), {}),
            "ButtonDeviceClass": SimpleNamespace(RESTART="restart"),
        },
        f"{package_name}.entity": {
            "ArcaneEntity": FakeEntity,
            "container_device_info": lambda *args: ({}, None),
        },
    }
    for name, attrs in attributes.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        modules[name] = module
    with patch.dict(sys.modules, modules):
        coordinator = importlib.import_module(f"{package_name}.coordinator")
        buttons = importlib.import_module(f"{package_name}.button")
    return coordinator, buttons


coordinator_module, buttons = load_modules()


class ActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = FakeEntry()
        self.client = Mock()
        self.coordinator = coordinator_module.ArcaneCoordinator(
            object(), self.entry, self.client
        )
        self.coordinator.data = coordinator_module.ArcaneData(
            summary={}, dashboard={},
            containers={"scan": {"id": "abc", "state": "running", "image": "scan:latest"}},
            container_counts={}, image_counts={}, volume_counts={}, network_counts={},
            project_counts={}, port_count=None, version={}, docker_info={},
        )

    async def asyncTearDown(self):
        for task in self.entry.tasks:
            task.cancel()
        await asyncio.gather(*self.entry.tasks, return_exceptions=True)

    async def test_start_and_resume_use_distinct_actions(self):
        self.client.async_start_container = AsyncMock()
        self.client.async_unpause_container = AsyncMock()
        with patch.object(self.coordinator, "_schedule_action_refresh") as schedule:
            await buttons.ArcaneContainerStartButton(self.coordinator, "scan").async_press()
            self.client.async_start_container.assert_awaited_once_with("0", "abc")
            self.client.async_unpause_container.assert_not_awaited()
            await buttons.ArcaneContainerResumeButton(self.coordinator, "scan").async_press()
            self.client.async_unpause_container.assert_awaited_once_with("0", "abc")
            self.assertEqual(schedule.call_count, 2)

    async def test_successful_lifecycle_actions_schedule_checks(self):
        for action in ("start", "stop", "restart", "pause", "unpause"):
            with self.subTest(action=action):
                method = f"async_{action}_container"
                setattr(self.client, method, AsyncMock())
                with patch.object(self.coordinator, "_schedule_action_refresh") as schedule:
                    await getattr(self.coordinator, method)("scan")
                    getattr(self.client, method).assert_awaited_once_with("0", "abc")
                    schedule.assert_called_once_with("scan")

    async def test_failed_action_does_not_schedule_or_replay(self):
        self.client.async_stop_container = AsyncMock(
            side_effect=coordinator_module.ArcaneApiError("stop failed")
        )
        with patch.object(self.coordinator, "_schedule_action_refresh") as schedule:
            with self.assertRaises(coordinator_module.ArcaneApiError):
                await self.coordinator.async_stop_container("scan")
            schedule.assert_not_called()
            self.client.async_stop_container.assert_awaited_once()

    async def test_missing_container_does_not_send_action(self):
        self.client.async_start_container = AsyncMock()
        with self.assertRaisesRegex(coordinator_module.ArcaneApiError, "no longer available"):
            await self.coordinator.async_start_container("missing")
        self.client.async_start_container.assert_not_awaited()

    async def test_poll_times_and_eventual_state_without_full_refresh(self):
        clock = SimpleNamespace(now=0.0)
        times = []

        async def sleep(delay):
            clock.now += delay

        async def read_state(*args):
            times.append(clock.now)
            return "running" if len(times) < 3 else "exited"

        self.client.async_get_container_state = AsyncMock(side_effect=read_state)
        fake_asyncio = SimpleNamespace(
            get_running_loop=lambda: SimpleNamespace(time=lambda: clock.now),
            sleep=sleep, current_task=asyncio.current_task,
        )
        original = self.coordinator.data
        with patch.object(coordinator_module, "asyncio", fake_asyncio):
            await self.coordinator._async_poll_action_state("scan")
        self.assertEqual(times, [0, 3, 6, 9, 12, 15])
        self.assertEqual(self.coordinator.data.containers["scan"]["state"], "exited")
        self.assertEqual(original.containers["scan"]["state"], "running")
        self.assertEqual(self.coordinator.data.containers["scan"]["image"], "scan:latest")
        self.assertEqual(len(self.coordinator.snapshots), 6)

    async def test_transient_poll_failure_is_retried(self):
        self.client.async_get_container_state = AsyncMock(side_effect=[
            coordinator_module.ArcaneApiError("temporary"),
            "exited", "exited", "exited", "exited", "exited",
        ])
        with patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()):
            await self.coordinator._async_poll_action_state("scan")
        self.assertEqual(self.client.async_get_container_state.await_count, 6)
        self.assertEqual(len(self.coordinator.snapshots), 5)

    async def test_removed_container_ends_checks(self):
        self.coordinator.data.containers.clear()
        self.client.async_get_container_state = AsyncMock()
        await self.coordinator._async_poll_action_state("scan")
        self.client.async_get_container_state.assert_not_awaited()

    async def test_replaced_container_does_not_receive_old_state(self):
        async def read_state(*args):
            self.coordinator.data.containers["scan"] = {"id": "new", "state": "running"}
            return "exited"

        self.client.async_get_container_state = AsyncMock(side_effect=read_state)
        with patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()):
            await self.coordinator._async_poll_action_state("scan")
        self.assertEqual(self.coordinator.snapshots[0].containers["scan"]["id"], "new")
        self.assertEqual(
            self.client.async_get_container_state.await_args_list[1].args, ("0", "new")
        )

    async def test_new_action_cancels_previous_checks(self):
        self.coordinator._schedule_action_refresh("scan")
        old = self.coordinator._action_refresh_tasks["scan"]
        self.coordinator._schedule_action_refresh("scan")
        new = self.coordinator._action_refresh_tasks["scan"]
        self.assertIsNot(old, new)
        self.assertTrue(old.cancelling())
        self.assertEqual(len(self.entry.tasks), 2)

    async def test_poll_task_cleans_up_after_cancellation(self):
        reached_delay = asyncio.Event()

        async def wait_for_next_poll(delay):
            reached_delay.set()
            await asyncio.Future()

        self.client.async_get_container_state = AsyncMock(return_value="exited")
        fake_asyncio = SimpleNamespace(
            get_running_loop=asyncio.get_running_loop,
            sleep=wait_for_next_poll, current_task=asyncio.current_task,
        )
        with patch.object(coordinator_module, "asyncio", fake_asyncio):
            self.coordinator._schedule_action_refresh("scan")
            await reached_delay.wait()
            task = self.coordinator._action_refresh_tasks["scan"]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertNotIn("scan", self.coordinator._action_refresh_tasks)

    async def test_button_availability_for_each_state(self):
        button_classes = {
            "start": buttons.ArcaneContainerStartButton,
            "resume": buttons.ArcaneContainerResumeButton,
            "pause": buttons.ArcaneContainerPauseButton,
            "stop": buttons.ArcaneContainerStopButton,
            "restart": buttons.ArcaneContainerRestartButton,
        }
        expected = {
            "running": {"pause", "stop", "restart"},
            "paused": {"resume", "stop"},
            "exited": {"start", "restart"},
            "created": {"start", "restart"},
            "dead": set(),
        }
        for state, allowed in expected.items():
            with self.subTest(state=state):
                self.coordinator.data.containers["scan"]["state"] = state
                actual = {
                    name for name, cls in button_classes.items()
                    if cls(self.coordinator, "scan").available
                }
                self.assertEqual(actual, allowed)


if __name__ == "__main__":
    unittest.main()
