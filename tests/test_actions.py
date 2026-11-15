"""Lifecycle behavior using minimal HA doubles, without a running HA instance."""

import asyncio
from dataclasses import dataclass
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
        self.async_request_refresh = AsyncMock()
        self.async_update_listeners = Mock()

    def async_set_updated_data(self, data):
        self.data = data
        self.snapshots.append(data)


class FakeEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return True


@dataclass(frozen=True, kw_only=True)
class FakeSensorDescription:
    key: str
    translation_key: str
    icon: str
    native_unit_of_measurement: str | None = None
    entity_category: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    options: list[str] | None = None


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
        "homeassistant.components.sensor": {
            "SensorEntity": type("SensorEntity", (), {}),
            "SensorEntityDescription": FakeSensorDescription,
            "SensorDeviceClass": SimpleNamespace(DATA_SIZE="data_size", ENUM="enum"),
            "SensorStateClass": SimpleNamespace(MEASUREMENT="measurement"),
        },
        "homeassistant.const": {
            "PERCENTAGE": "%",
            "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"),
            "UnitOfInformation": SimpleNamespace(BYTES="B"),
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
        sensors = importlib.import_module(f"{package_name}.sensor")
    return coordinator, buttons, sensors


coordinator_module, buttons, sensors = load_modules()


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

    async def test_cleanup_submission_is_not_claimed_complete(self):
        self.client.async_prune = AsyncMock(return_value={"activityId": "prune-1"})
        with patch.object(self.coordinator, "_async_watch_cleanup", new=AsyncMock()):
            result = await self.coordinator.async_prune({"images": {"mode": "dangling"}})
            self.assertEqual(result, {"accepted": True, "activity_id": "prune-1"})
            self.assertEqual(self.coordinator.data.cleanup_status, "submitted")
            self.assertEqual(self.coordinator.data.cleanup_activity_id, "prune-1")

    async def test_cleanup_rejects_duplicate_submission_while_monitoring(self):
        pending = asyncio.create_task(asyncio.sleep(60))
        self.entry.tasks.append(pending)
        self.coordinator._cleanup_task = pending
        self.client.async_prune = AsyncMock()
        with self.assertRaisesRegex(coordinator_module.ArcaneApiError, "already"):
            await self.coordinator.async_prune({"images": {"mode": "dangling"}})
        self.client.async_prune.assert_not_awaited()

    async def test_cleanup_success_refreshes_storage_without_repeating_post(self):
        self.client.async_get_activity = AsyncMock(side_effect=[
            {"status": "running"}, {"status": "success"},
        ])
        self.client.async_prune = AsyncMock()
        with patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()):
            await self.coordinator._async_watch_cleanup("prune-1")
        self.assertEqual(self.coordinator.data.cleanup_status, "success")
        self.client.invalidate_disk_usage.assert_called_once_with("0")
        self.coordinator.async_request_refresh.assert_awaited_once()
        self.client.async_prune.assert_not_awaited()

    async def test_cleanup_timeout_is_unknown_not_success(self):
        self.client.async_get_activity = AsyncMock(return_value={"status": "running"})
        with patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()):
            await self.coordinator._async_watch_cleanup("prune-1")
        self.assertEqual(self.coordinator.data.cleanup_status, "unknown")
        self.assertEqual(self.client.async_get_activity.await_count, 24)

    async def test_cleanup_failure_is_visible(self):
        self.client.async_get_activity = AsyncMock(return_value={"status": "failed"})
        with patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()):
            await self.coordinator._async_watch_cleanup("prune-1")
        self.assertEqual(self.coordinator.data.cleanup_status, "failed")

    async def test_optional_storage_failures_do_not_take_integration_offline(self):
        for name in (
            "async_get_update_summary", "async_get_updates_by_refs",
            "async_get_dashboard", "async_get_container_counts", "async_get_image_counts",
            "async_get_volume_counts", "async_get_network_counts", "async_get_project_counts",
            "async_get_version", "async_get_docker_info",
            "async_get_security_summary", "async_get_security_overview",
        ):
            setattr(self.client, name, AsyncMock(return_value={}))
        self.client.async_list_containers = AsyncMock(return_value=[])
        self.client.async_get_port_count = AsyncMock(return_value=0)
        self.client.async_prune = AsyncMock()
        self.client.async_scan_image = AsyncMock()
        for name in ("async_get_system_stats", "async_get_disk_usage"):
            setattr(self.client, name, AsyncMock(
                side_effect=coordinator_module.ArcaneApiError("permission denied")
            ))
        result = await self.coordinator._async_update_data()
        self.assertEqual(result.storage, {})
        self.assertEqual(result.port_count, 0)
        self.client.async_prune.assert_not_awaited()
        self.client.async_scan_image.assert_not_awaited()

    async def test_storage_sensors_read_normalized_values_and_keep_missing_unknown(self):
        self.coordinator.data.storage = {"disk_free": 123, "disk_percent": 55.6}
        descriptions = {description.key: description for description in sensors.SENSORS}
        for key, expected in (("disk_free", 123), ("disk_percent", 55.6), ("disk_total", None)):
            entity = sensors.ArcaneStatisticsSensor(self.coordinator, descriptions[key])
            self.assertEqual(entity.native_value, expected)
        self.assertEqual(descriptions["disk_free"].native_unit_of_measurement, "B")
        self.assertEqual(descriptions["disk_percent"].native_unit_of_measurement, "%")

    async def test_cleanup_sensor_exposes_status_and_activity_id(self):
        self.coordinator.data.cleanup_status = "running"
        self.coordinator.data.cleanup_activity_id = "job-123"
        description = next(
            item for item in sensors.SENSORS if item.key == "cleanup_status"
        )
        entity = sensors.ArcaneStatisticsSensor(self.coordinator, description)
        self.assertEqual(entity.native_value, "running")
        self.assertEqual(entity.extra_state_attributes, {"activity_id": "job-123"})

    async def test_security_button_never_runs_on_construction(self):
        self.client.uses_api_v3 = True
        self.coordinator.async_scan_security = AsyncMock()
        button = buttons.ArcaneSecurityScanButton(self.coordinator)
        self.assertTrue(button.available)
        self.coordinator.async_scan_security.assert_not_awaited()
        await button.async_press()
        self.coordinator.async_scan_security.assert_awaited_once()

    async def test_security_button_disabled_for_legacy_api_and_busy_task(self):
        button = buttons.ArcaneSecurityScanButton(self.coordinator)
        self.client.uses_api_v3 = False
        self.assertFalse(button.available)
        self.client.uses_api_v3 = True
        task = asyncio.create_task(asyncio.sleep(60))
        self.entry.tasks.append(task)
        self.coordinator._security_scan_task = task
        self.assertFalse(button.available)
        self.client.async_list_images = AsyncMock()
        with self.assertRaisesRegex(coordinator_module.ArcaneApiError, "already"):
            await self.coordinator.async_scan_security()
        self.client.async_list_images.assert_not_awaited()

    async def test_security_scan_deduplicates_image_ids_before_submitting(self):
        self.client.uses_api_v3 = True
        self.client.async_list_images = AsyncMock(return_value=[
            {"id": "used"}, {"id": "unused"}, {"id": "used"},
        ])
        with patch.object(self.coordinator, "_async_submit_security_scans", new=AsyncMock()) as submit:
            await self.coordinator.async_scan_security()
            await asyncio.gather(*self.entry.tasks)
            submit.assert_awaited_once_with(["used", "unused"])
        self.assertEqual(self.coordinator.data.security_scan["total_images"], 2)

    async def test_invalid_or_empty_scan_list_does_not_submit(self):
        self.client.uses_api_v3 = True
        self.client.async_scan_image = AsyncMock()
        for images in ([], [{"id": "one"}, {}]):
            self.client.async_list_images = AsyncMock(return_value=images)
            with self.assertRaises(coordinator_module.ArcaneApiError):
                await self.coordinator.async_scan_security()
        self.client.async_scan_image.assert_not_awaited()
        self.assertEqual(self.entry.tasks, [])

    async def test_security_worker_reports_partial_and_never_retries(self):
        self.client.async_scan_image = AsyncMock(side_effect=[
            {"status": "pending"}, coordinator_module.ArcaneApiError("failed"),
        ])
        with (
            patch.object(self.coordinator, "_async_refresh_security", new=AsyncMock()) as refresh,
            patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()) as sleep,
        ):
            await self.coordinator._async_submit_security_scans(["a", "b"])
        self.assertEqual(self.client.async_scan_image.await_count, 2)
        self.assertEqual(self.coordinator.data.security_scan, {
            "status": "partial", "total_images": 2, "accepted_images": 1, "failed_images": 1,
        })
        self.assertEqual(refresh.await_count, 5)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [15, 45, 60, 180])

    async def test_security_auth_failure_stops_remaining_submissions(self):
        self.client.async_scan_image = AsyncMock(
            side_effect=coordinator_module.ArcaneAuthError("permission denied")
        )
        with patch.object(self.coordinator, "_async_refresh_security", new=AsyncMock()):
            await self.coordinator._async_submit_security_scans(["a", "b", "c"])
        self.client.async_scan_image.assert_awaited_once_with("0", "a")
        self.assertEqual(self.coordinator.data.security_scan["status"], "failed")
        self.assertEqual(self.coordinator.data.security_scan["failed_images"], 3)

    async def test_security_accepted_is_not_reported_as_scan_completion(self):
        self.client.async_scan_image = AsyncMock(return_value={"status": "pending"})
        with (
            patch.object(self.coordinator, "_async_refresh_security", new=AsyncMock()),
            patch.object(coordinator_module.asyncio, "sleep", new=AsyncMock()),
        ):
            await self.coordinator._async_submit_security_scans(["a"])
        self.assertEqual(self.coordinator.data.security_scan["status"], "submitted")

    async def test_security_sensor_and_priority_attributes(self):
        self.coordinator.data.security = {
            "risk_score": 84,
            "details": {"highest_priority_cve": "CVE-2026-1234", "images_scanned": 11},
            "threat_intel_status": "not_synced",
        }
        description = next(
            item for item in sensors.SENSORS if item.key == "security_risk_score"
        )
        entity = sensors.ArcaneStatisticsSensor(self.coordinator, description)
        self.assertEqual(entity.native_value, 84)
        self.assertEqual(entity.extra_state_attributes["highest_priority_cve"], "CVE-2026-1234")
        self.assertEqual(entity.extra_state_attributes["threat_intel_status"], "not_synced")


if __name__ == "__main__":
    unittest.main()
