"""API regression tests without a running Home Assistant or Docker host.

Run with: python -m unittest discover -s tests -v
"""

import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from aiohttp import ClientConnectionError

# Load the standalone client without importing the HA integration's __init__.
SPEC = importlib.util.spec_from_file_location(
    "arcane_api",
    Path(__file__).resolve().parents[1] / "custom_components/arcane/api.py",
)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class Response:
    def __init__(self, data=None, status=200):
        self.status = status
        self.json = AsyncMock(return_value=data if data is not None else {})
        self.text = AsyncMock(return_value="error")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = unittest.mock.Mock()
        self.session.request = AsyncMock(return_value=Response({"data": {}}))
        self.client = api.ArcaneApiClient(
            self.session, "https://arcane.example/api/", "secret", verify_ssl=False
        )

    def assert_request(self, method, path, params=None, timeout=30, json=None):
        call = self.session.request.call_args
        self.assertEqual(call.args, (method, f"https://arcane.example/api{path}"))
        self.assertEqual(call.kwargs["params"], params)
        self.assertEqual(call.kwargs["json"], json)
        self.assertEqual(call.kwargs["timeout"].total, timeout)
        self.assertEqual(call.kwargs["headers"], {"X-API-Key": "secret"})
        self.assertFalse(call.kwargs["ssl"])

    async def test_v3_summary_detects_api_for_existing_entry(self):
        self.session.request.side_effect = [
            Response({"data": [{"id": "0"}]}),
            Response({"data": {"totalImages": 4}}),
        ]
        self.assertEqual(
            await self.client.async_get_update_summary("0"), {"totalImages": 4}
        )
        self.assertTrue(self.client.uses_api_v3)
        self.assert_request("GET", "/agents/0/images/updates/summary")

    async def test_v3_environment_selection(self):
        self.session.request.return_value = Response({"data": [{"id": "remote"}]})
        self.assertEqual(
            await self.client.async_list_environments(), [{"id": "remote"}]
        )
        self.assert_request("GET", "/agents", {"start": 0, "limit": 200})

    async def test_concurrent_initial_poll_only_probes_once(self):
        await asyncio.gather(
            self.client.async_get_update_summary("0"),
            self.client.async_list_containers("0"),
        )
        probes = [
            call for call in self.session.request.call_args_list
            if call.args[1] == "https://arcane.example/api/agents"
        ]
        self.assertEqual(len(probes), 1)

    async def test_v2_detection_and_routes(self):
        self.session.request.side_effect = [
            Response({"error": "Not found"}, 404),
            Response({"data": [{"id": "0"}]}),
            Response({"data": {"totalImages": 2}}),
        ]
        await self.client.async_get_update_summary("0")
        self.assertFalse(self.client.uses_api_v3)
        self.assert_request("GET", "/environments/0/image-updates/summary")

    async def test_auth_failure_does_not_fall_back(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.session.request.reset_mock()
                self.session.request.return_value = Response(status=status)
                with self.assertRaises(api.ArcaneAuthError):
                    await self.client.async_list_environments()
                self.assertEqual(self.session.request.call_count, 1)
                self.assertIsNone(self.client._api_v3)

    async def test_server_error_does_not_fall_back(self):
        self.session.request.return_value = Response({"error": "Unavailable"}, 500)
        with self.assertRaisesRegex(api.ArcaneApiError, "Unavailable"):
            await self.client.async_list_environments()
        self.assertEqual(self.session.request.call_count, 1)

    async def test_connection_failure_does_not_fall_back(self):
        self.session.request.side_effect = ClientConnectionError("offline")
        with self.assertRaises(api.ArcaneConnectionError):
            await self.client.async_list_environments()
        self.assertEqual(self.session.request.call_count, 1)

    async def test_failed_detection_is_retried_on_next_poll(self):
        self.session.request.return_value = Response(status=500)
        with self.assertRaises(api.ArcaneApiError):
            await self.client.async_get_update_summary("0")
        self.session.request.return_value = Response({"data": {}})
        await self.client.async_get_update_summary("0")
        self.assertTrue(self.client.uses_api_v3)

    async def test_v3_image_refs_batches_and_deduplicates(self):
        refs = [f"image:{i}" for i in range(26)]
        self.session.request.side_effect = [
            Response(),
            Response({"data": {refs[0]: {"hasUpdate": True}}}),
            Response({"data": {refs[-1]: {"hasUpdate": False}}}),
        ]
        result = await self.client.async_get_updates_by_refs("remote", refs + refs)
        self.assertEqual(len(result), 2)
        self.assertEqual(self.session.request.call_count, 3)
        self.assert_request(
            "GET", "/agents/remote/images/updates/refs",
            {"imageRefs": refs[-1]}, timeout=60,
        )

    async def test_v3_check_all_uses_query_action(self):
        await self.client.async_check_all_updates("0")
        self.assert_request(
            "POST", "/agents/0/images/updates/check",
            {"action": "all"}, timeout=180, json={},
        )

    async def test_v3_update_uses_updater_not_raw_redeploy(self):
        await self.client.async_update_container("remote", "container-id")
        self.assert_request(
            "POST", "/agents/remote/containers/container-id/update", timeout=300
        )

    async def test_v3_arcane_self_update_preserves_updater_strategy(self):
        self.session.request.side_effect = [
            Response(),
            Response({"success": True, "data": {"updated": 1, "failed": 0}}),
        ]
        result = await self.client.async_update_container("0", "arcane-manager-id")
        self.assertEqual(result["data"]["failed"], 0)
        self.assert_request(
            "POST", "/agents/0/containers/arcane-manager-id/update", timeout=300
        )
        self.assertFalse(any(
            "/redeploy" in call.args[1]
            for call in self.session.request.call_args_list
        ))

    async def test_v3_lifecycle_actions(self):
        for action in ("start", "pause", "unpause", "restart", "stop"):
            with self.subTest(action=action):
                await getattr(self.client, f"async_{action}_container")("0", "abc")
                self.assert_request(
                    "POST", "/agents/0/containers/abc", {"action": action},
                    timeout=60 if action in ("start", "restart", "stop") else 30,
                )

    async def test_failed_action_is_not_replayed(self):
        self.session.request.side_effect = [
            Response(),
            Response({"error": "Container not found"}, 404),
        ]
        with self.assertRaisesRegex(api.ArcaneApiError, "Container not found"):
            await self.client.async_update_container("0", "missing")
        self.assertEqual(self.session.request.call_count, 2)
        self.assert_request(
            "POST", "/agents/0/containers/missing/update", timeout=300
        )

    async def test_v2_actions_are_unchanged(self):
        self.client._api_v3 = False
        await self.client.async_update_container("0", "abc")
        self.assert_request(
            "POST", "/environments/0/containers/abc/update", timeout=300
        )
        for action in ("start", "pause", "unpause", "restart", "stop"):
            await getattr(self.client, f"async_{action}_container")("0", "abc")
            self.assert_request(
                "POST", f"/environments/0/containers/abc/{action}",
                timeout=60 if action in ("start", "restart", "stop") else 30,
            )
        await self.client.async_check_all_updates("0")
        self.assert_request(
            "POST", "/environments/0/image-updates/check-all", timeout=180, json={}
        )
        await self.client.async_get_updates_by_refs("0", ["nginx:latest"])
        self.assert_request(
            "GET", "/environments/0/image-updates/by-refs",
            {"imageRefs": "nginx:latest"}, timeout=60,
        )

    async def test_container_pagination(self):
        self.session.request.side_effect = [
            Response(),
            Response({"data": [{"id": "a"}], "pagination": {"totalItems": 2}}),
            Response({"data": [{"id": "b"}], "pagination": {"totalItems": 2}}),
        ]
        self.assertEqual(
            await self.client.async_list_containers("0"), [{"id": "a"}, {"id": "b"}]
        )
        self.assert_request(
            "GET", "/agents/0/containers",
            {"start": 1, "limit": 200, "includeInternal": "false"},
        )

    async def test_optional_statistics_v3_paths(self):
        for name, path in (
            ("container_counts", "containers/counts"),
            ("image_counts", "images/counts"),
            ("volume_counts", "volumes/counts"),
            ("network_counts", "networks/counts"),
            ("project_counts", "projects/counts"),
            ("version", "version"),
            ("docker_info", "system/docker/info"),
            ("port_count", "ports"),
        ):
            with self.subTest(name=name):
                await getattr(self.client, f"async_get_{name}")("0")
                self.assert_request(
                    "GET", f"/agents/0/{path}",
                    {"start": 0, "limit": 1} if name == "port_count" else None,
                )

    async def test_v3_does_not_request_removed_dashboard(self):
        self.assertEqual(await self.client.async_get_dashboard("0"), {})
        self.assertEqual(self.session.request.call_count, 1)
        self.assert_request("GET", "/agents", {"start": 0, "limit": 1})

    async def test_v2_dashboard_is_retained(self):
        self.client._api_v3 = False
        self.session.request.return_value = Response({"data": {"actionItems": {}}})
        self.assertEqual(
            await self.client.async_get_dashboard("0"), {"actionItems": {}}
        )
        self.assert_request("GET", "/environments/0/dashboard")

    async def test_live_container_state_formats(self):
        self.client._api_v3 = True
        for state in ("exited", {"Status": "exited"}, {"status": "exited"}):
            with self.subTest(state=state):
                self.session.request.return_value = Response({"data": {"state": state}})
                self.assertEqual(
                    await self.client.async_get_container_state("0", "abc"), "exited"
                )
                self.assert_request("GET", "/agents/0/containers/abc", timeout=3)

    async def test_live_state_rejects_missing_status(self):
        self.client._api_v3 = False
        self.session.request.return_value = Response({"data": {"state": {}}})
        with self.assertRaisesRegex(api.ArcaneApiError, "no container state"):
            await self.client.async_get_container_state("0", "abc")
        self.assert_request("GET", "/environments/0/containers/abc", timeout=3)


if __name__ == "__main__":
    unittest.main()
