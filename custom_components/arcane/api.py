"""Async client for the Arcane 2.x API."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout

REFS_PER_REQUEST = 25


class ArcaneApiError(Exception):
    """Base Arcane API exception."""


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
            raise ArcaneAuthError(f"Arcane returned HTTP {response.status}")

        if response.status >= 400:
            try:
                body = await response.json()
                detail = body.get("detail") or body.get("title") or str(body)
            except (ValueError, ClientError):
                detail = await response.text()
            raise ArcaneApiError(
                f"Arcane returned HTTP {response.status}: {detail}"
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
