"""Async client for the Arcane 2.x and 3.x APIs."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import time
from typing import Any

from aiohttp import (
    ClientError,
    ClientResponse,
    ClientSession,
    ClientTimeout,
    WSServerHandshakeError,
)

REFS_PER_REQUEST = 25
DISK_USAGE_CACHE_SECONDS = 900


class ArcaneApiError(Exception):
    """Base Arcane API exception."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ArcaneAuthError(ArcaneApiError):
    """Raised when Arcane rejects the API key."""


class ArcaneConnectionError(ArcaneApiError):
    """Raised when Arcane cannot be reached."""


class ArcaneApiClient:
    """Small client covering the API used by this integration."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        api_key: str,
        verify_ssl: bool = True,
    ) -> None:
        self._session = session
        self.base_url = self.normalize_url(base_url)
        self._api_key = api_key
        self._verify_ssl = verify_ssl
        self._api_v3: bool | None = None
        self._detection_lock = asyncio.Lock()
        self._disk_usage_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._disk_usage_lock = asyncio.Lock()

    @property
    def uses_api_v3(self) -> bool:
        """Return whether this client has detected Arcane 3."""
        return self._api_v3 is True

    async def _async_detect_api(self) -> None:
        """Probe a read-only endpoint once, including for existing HA entries."""
        async with self._detection_lock:
            if self._api_v3 is not None:
                return
            try:
                await self._async_request(
                    "GET", "/agents", params={"start": 0, "limit": 1}
                )
            except ArcaneApiError as err:
                if err.status != 404:
                    raise
                # Never interpret authentication or connection failures as v2.
                await self._async_request(
                    "GET", "/environments", params={"start": 0, "limit": 1}
                )
                self._api_v3 = False
            else:
                self._api_v3 = True

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize an Arcane base URL entered by the user."""
        normalized = url.strip().rstrip("/")
        if normalized.endswith("/api"):
            normalized = normalized[:-4]
        return normalized

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """Translate the integration's v2 routes before sending any action."""
        await self._async_detect_api()
        if self.uses_api_v3:
            path = path.replace("/environments", "/agents", 1)
            path = path.replace("/image-updates/by-refs", "/images/updates/refs")
            path = path.replace("/image-updates/summary", "/images/updates/summary")
            if path.endswith("/image-updates/check-all"):
                path = path.removesuffix("/image-updates/check-all")
                path += "/images/updates/check"
                params = {**(params or {}), "action": "all"}
            if method == "POST" and "/containers/" in path:
                resource, action = path.rsplit("/", 1)
                # /update belongs to Arcane's updater, not the raw container
                # /redeploy route. It preserves project and self-update handling.
                if action in ("start", "pause", "unpause", "restart", "stop"):
                    path = resource
                    params = {**(params or {}), "action": action}
        # Do not retry mutating requests against another API after a failure.
        return await self._async_request(
            method, path, params=params, json=json, timeout=timeout
        )

    async def _async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api{path}"
        try:
            response = await self._session.request(
                method,
                url,
                headers={"X-API-Key": self._api_key},
                params=params,
                json=json,
                ssl=self._verify_ssl,
                timeout=ClientTimeout(total=timeout),
            )
        except (ClientError, TimeoutError) as err:
            raise ArcaneConnectionError(str(err)) from err

        return await self._handle_response(response)

    @staticmethod
    async def _handle_response(response: ClientResponse) -> dict[str, Any]:
        if response.status in (401, 403):
            raise ArcaneAuthError(
                f"Arcane returned HTTP {response.status}", status=response.status
            )

        if response.status >= 400:
            try:
                body = await response.json()
                data = body.get("data") if isinstance(body, dict) else None
                detail = (
                    (data.get("error") if isinstance(data, dict) else None)
                    or body.get("error")
                    or body.get("detail")
                    or body.get("title")
                    or body.get("message")
                    or str(body)
                )
            except (ValueError, ClientError, AttributeError):
                detail = await response.text()
            raise ArcaneApiError(
                f"Arcane returned HTTP {response.status}: {detail}",
                status=response.status,
            )

        if response.status == 204:
            return {}

        try:
            payload = await response.json()
        except (ValueError, ClientError) as err:
            raise ArcaneApiError("Arcane returned invalid JSON") from err

        if isinstance(payload, dict) and payload.get("success") is False:
            raise ArcaneApiError(str(payload.get("message", "Arcane request failed")))
        return payload

    async def async_list_environments(self) -> list[dict[str, Any]]:
        """Return every Arcane environment visible to the API key."""
        payload = await self._request(
            "GET", "/environments", params={"start": 0, "limit": 200}
        )
        return list(payload.get("data") or [])

    async def async_get_update_summary(
        self, environment_id: str
    ) -> dict[str, Any]:
        """Return cached registry update counters."""
        payload = await self._request(
            "GET", f"/environments/{environment_id}/image-updates/summary"
        )
        return dict(payload.get("data") or {})

    async def async_get_updates_by_refs(
        self, environment_id: str, image_refs: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        """Return persisted update records keyed by image reference.

        The container list embeds a stale updateInfo snapshot, so this endpoint
        is the authoritative source for hasUpdate and latestDigest.
        """
        refs = [ref for ref in dict.fromkeys(image_refs) if ref]
        records: dict[str, dict[str, Any]] = {}

        for index in range(0, len(refs), REFS_PER_REQUEST):
            chunk = refs[index : index + REFS_PER_REQUEST]
            payload = await self._request(
                "GET",
                f"/environments/{environment_id}/image-updates/by-refs",
                params={"imageRefs": ",".join(chunk)},
                timeout=60,
            )
            data = payload.get("data") or {}
            if isinstance(data, dict):
                for ref, info in data.items():
                    if isinstance(info, dict):
                        records[str(ref)] = info

        return records

    async def async_get_dashboard(self, environment_id: str) -> dict[str, Any]:
        """Return Arcane's actionable dashboard snapshot."""
        await self._async_detect_api()
        if self.uses_api_v3:
            # Arcane 3 removed the monolithic dashboard endpoint.
            return {}
        return await self._async_get_data(environment_id, "dashboard")

    async def _async_get_data(self, environment_id: str, path: str) -> dict[str, Any]:
        """Return the data object from an environment endpoint."""
        payload = await self._request(
            "GET", f"/environments/{environment_id}/{path}"
        )
        return dict(payload.get("data") or {})

    async def async_get_container_counts(self, environment_id: str) -> dict[str, Any]:
        """Return container status counters."""
        return await self._async_get_data(environment_id, "containers/counts")

    async def async_get_image_counts(self, environment_id: str) -> dict[str, Any]:
        """Return image usage counters."""
        return await self._async_get_data(environment_id, "images/counts")

    async def async_get_volume_counts(self, environment_id: str) -> dict[str, Any]:
        """Return volume usage counters."""
        return await self._async_get_data(environment_id, "volumes/counts")

    async def async_get_network_counts(self, environment_id: str) -> dict[str, Any]:
        """Return network usage counters."""
        return await self._async_get_data(environment_id, "networks/counts")

    async def async_get_project_counts(self, environment_id: str) -> dict[str, Any]:
        """Return project status counters."""
        return await self._async_get_data(environment_id, "projects/counts")

    async def async_get_version(self, environment_id: str) -> dict[str, Any]:
        """Return Arcane build and version information."""
        return await self._async_get_data(environment_id, "version")

    async def async_get_docker_info(self, environment_id: str) -> dict[str, Any]:
        """Return Docker Engine information (this endpoint is not wrapped)."""
        return await self._request(
            "GET", f"/environments/{environment_id}/system/docker/info"
        )

    async def async_get_port_count(self, environment_id: str) -> int:
        """Return the number of published port mappings."""
        payload = await self._request(
            "GET",
            f"/environments/{environment_id}/ports",
            params={"start": 0, "limit": 1},
        )
        pagination = payload.get("pagination") or {}
        return int(pagination.get("totalItems", len(payload.get("data") or [])))

    async def async_get_system_stats(self, environment_id: str) -> dict[str, Any]:
        """Read one stats snapshot and close the WebSocket, not a live stream."""
        await self._async_detect_api()
        prefix = "agents" if self.uses_api_v3 else "environments"
        url = f"{self.base_url}/api/{prefix}/{environment_id}/ws/system/stats"
        try:
            async with asyncio.timeout(10):
                async with self._session.ws_connect(
                    url, headers={"X-API-Key": self._api_key},
                    ssl=self._verify_ssl, max_msg_size=65536,
                ) as websocket:
                    payload = await websocket.receive_json()
        except WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise ArcaneAuthError(
                    f"Arcane stats returned HTTP {err.status}", status=err.status
                ) from err
            raise ArcaneApiError(f"Arcane stats returned HTTP {err.status}") from err
        except (ClientError, TimeoutError, ValueError, TypeError) as err:
            raise ArcaneApiError(f"Arcane system stats unavailable: {err}") from err
        if not isinstance(payload, dict) or not any(
            key in payload for key in ("diskTotal", "memoryTotal", "cpuUsage")
        ):
            raise ArcaneApiError("Arcane returned invalid system stats")
        return payload

    def invalidate_disk_usage(self, environment_id: str) -> None:
        """Discard the cached reclaimable-size estimate after a cleanup."""
        self._disk_usage_cache.pop(environment_id, None)

    async def async_get_disk_usage(self, environment_id: str) -> dict[str, Any]:
        """Limit potentially expensive Docker disk-usage calls to every 15 min."""
        async with self._disk_usage_lock:
            cached = self._disk_usage_cache.get(environment_id)
            if (
                cached is not None
                and time.monotonic() - cached[0] < DISK_USAGE_CACHE_SECONDS
            ):
                return dict(cached[1])
            data = await self._async_get_data(environment_id, "system/df")
            self._disk_usage_cache[environment_id] = (time.monotonic(), dict(data))
            return data

    async def async_prune(
        self, environment_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        """Submit one selective cleanup; never retry a destructive operation."""
        await self._async_detect_api()
        if not self.uses_api_v3:
            raise ArcaneApiError("Selective cleanup requires Arcane 3.0 or newer")
        payload = await self._request(
            "POST", f"/environments/{environment_id}/system/prune",
            json=request, timeout=60,
        )
        data = dict(payload.get("data") or {})
        if data.get("success") is False:
            raise ArcaneApiError(str(data.get("errors") or "Arcane cleanup failed"))
        return data

    async def async_get_activity(
        self, environment_id: str, activity_id: str
    ) -> dict[str, Any]:
        """Read cleanup progress without resubmitting the cleanup."""
        data = await self._async_get_data(
            environment_id, f"activities/{activity_id}"
        )
        return dict(data.get("activity") or {})

    async def async_get_security_summary(self, environment_id: str) -> dict[str, Any]:
        """Read stored scan counters; this does not start a scan."""
        return await self._async_get_data(environment_id, "vulnerabilities/summary")

    async def async_get_security_overview(self, environment_id: str) -> dict[str, Any]:
        """Read Arcane's computed priority and threat-intelligence overview."""
        return await self._async_get_data(environment_id, "vulnerabilities/overview")

    async def async_list_images(self, environment_id: str) -> list[dict[str, Any]]:
        """List images, including unused ones, before an explicit scan request."""
        images: list[dict[str, Any]] = []
        while True:
            payload = await self._request(
                "GET", f"/environments/{environment_id}/images",
                params={"start": len(images), "limit": 200},
            )
            page = list(payload.get("data") or [])
            images.extend(page)
            total = int((payload.get("pagination") or {}).get("totalItems", len(images)))
            if total > 1000 or len(images) > 1000:
                raise ArcaneApiError(
                    "Scanning more than 1000 images at once is not supported; use Arcane"
                )
            if not page or len(images) >= total:
                if len(images) < total:
                    raise ArcaneApiError("Arcane returned an incomplete image list")
                return images

    async def async_scan_image(
        self, environment_id: str, image_id: str
    ) -> dict[str, Any]:
        """Explicitly request a vulnerability scan; do not retry the POST."""
        await self._async_detect_api()
        if not self.uses_api_v3:
            raise ArcaneApiError("Security scans require Arcane 3.0 or newer")
        payload = await self._request(
            "POST",
            f"/environments/{environment_id}/images/{image_id}/vulnerabilities/scan",
            timeout=60,
        )
        data = dict(payload.get("data") or {})
        if data.get("status") not in ("pending", "scanning", "completed"):
            raise ArcaneApiError(str(data.get("error") or "Arcane did not accept the scan"))
        return data

    async def async_list_containers(
        self, environment_id: str
    ) -> list[dict[str, Any]]:
        """Return all non-internal containers, following Arcane pagination."""
        containers: list[dict[str, Any]] = []
        start = 0
        limit = 200

        while True:
            payload = await self._request(
                "GET",
                f"/environments/{environment_id}/containers",
                params={
                    "start": start,
                    "limit": limit,
                    "includeInternal": "false",
                },
            )
            page = list(payload.get("data") or [])
            containers.extend(page)
            pagination = payload.get("pagination") or {}
            total = int(pagination.get("totalItems", len(containers)))
            if not page or len(containers) >= total:
                break
            start += len(page)

        return containers

    async def async_check_all_updates(self, environment_id: str) -> None:
        """Ask Arcane to query registries for every image."""
        await self._request(
            "POST",
            f"/environments/{environment_id}/image-updates/check-all",
            json={},
            timeout=180,
        )

    async def async_update_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Apply Arcane's update strategy for one container."""
        return await self._request(
            "POST",
            f"/environments/{environment_id}/containers/{container_id}/update",
            timeout=300,
        )

    async def async_get_container_state(
        self, environment_id: str, container_id: str
    ) -> str:
        """Read the current state directly, without fetching all statistics."""
        payload = await self._request(
            "GET",
            f"/environments/{environment_id}/containers/{container_id}",
            timeout=3,
        )
        state = (payload.get("data") or {}).get("state")
        if isinstance(state, dict):
            # Docker inspect uses Status; some Arcane versions normalize it.
            state = state.get("status") or state.get("Status")
        if not isinstance(state, str) or not state:
            raise ArcaneApiError("Arcane returned no container state")
        return state

    async def async_start_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Start a stopped container (unpause is a separate action)."""
        return await self._request(
            "POST",
            f"/environments/{environment_id}/containers/{container_id}/start",
            timeout=60,
        )

    async def async_pause_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Pause a running container."""
        return await self._request(
            "POST",
            f"/environments/{environment_id}/containers/{container_id}/pause",
        )

    async def async_unpause_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Resume a paused container."""
        return await self._request(
            "POST",
            f"/environments/{environment_id}/containers/{container_id}/unpause",
        )

    async def async_restart_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Restart a container."""
        return await self._request(
            "POST",
            f"/environments/{environment_id}/containers/{container_id}/restart",
            timeout=60,
        )

    async def async_stop_container(
        self, environment_id: str, container_id: str
    ) -> dict[str, Any]:
        """Stop a running container."""
        return await self._request(
            "POST",
            f"/environments/{environment_id}/containers/{container_id}/stop",
            timeout=60,
        )
