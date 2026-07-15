from __future__ import annotations

from typing import Any

import httpx

# Strict runner-v1 control-plane API surface. These are the only paths the
# public runner is permitted to call on the portal control plane.
RUNNER_V1 = "/api/runner/v1"

# Local secrets and paths that must never cross either HTTP boundary. Every
# outbound control-plane body is screened against these keys before it is sent.
_FORBIDDEN_KEYS = frozenset(
    {
        "repo_path",
        "source",
        "provider_key",
        "openrouter_key",
        "openrouter-key",
        "access_key",
        "access-key",
        "api_key",
        "local_path",
        "path",
    }
)


class OrmasClient:
    """HTTP client for the Ormas control plane and gateway.

    ``base_url`` is either the control plane (``https://ormas.ai``) for the
    runner-v1 lifecycle, or the gateway (``https://ormas-gateway.fly.dev``) for
    routing previews. An httpx ``transport`` may be injected for testing.
    """

    def __init__(
        self,
        base_url: str,
        access_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_key}"},
            timeout=60.0,
            transport=transport,
        )

    @staticmethod
    def _screen(payload: dict[str, Any]) -> dict[str, Any]:
        """Fail closed if a local secret or path would leak over the wire."""
        for key in payload:
            if key in _FORBIDDEN_KEYS:
                raise ValueError(f"refusing to transmit local secret or path: {key}")
        return payload

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        return self._client.post(path, json=self._screen(payload))

    # -- gateway ------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        response = self._client.get("/health")
        response.raise_for_status()
        return response.json()

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post("/ormas/task", payload)
        response.raise_for_status()
        return response.json()

    def routing_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the gateway to route a brief WITHOUT sending any local secret."""
        response = self._post("/ormas/task", payload)
        response.raise_for_status()
        return response.json()

    # -- runner-v1 control plane -------------------------------------------
    def register_runner(
        self, runner_id: str, version: str, platform: str, capacity: int
    ) -> dict[str, Any]:
        response = self._post(
            f"{RUNNER_V1}/registrations",
            {
                "runner_id": runner_id,
                "version": version,
                "platform": platform,
                "capacity": capacity,
                "status": "healthy",
            },
        )
        response.raise_for_status()
        return response.json()

    def register_repo(
        self, repo_id: str, runner_id: str, alias: str, head_commit: str
    ) -> dict[str, Any]:
        response = self._post(
            f"{RUNNER_V1}/repositories",
            {
                "repo_id": repo_id,
                "runner_id": runner_id,
                "alias": alias,
                "head_commit": head_commit,
            },
        )
        response.raise_for_status()
        return response.json()

    def create_task(
        self,
        *,
        task_id: str,
        runner_id: str,
        repo_id: str,
        base_commit: str,
        brief: str,
        verify_command: str,
        allowed_paths: list[str],
        budget_usd: float,
    ) -> dict[str, Any]:
        response = self._post(
            f"{RUNNER_V1}/tasks",
            {
                "task_id": task_id,
                "runner_id": runner_id,
                "repo_id": repo_id,
                "base_commit": base_commit,
                "brief": brief,
                "verify_command": verify_command,
                "allowed_paths": list(allowed_paths),
                "budget_usd": budget_usd,
            },
        )
        response.raise_for_status()
        return response.json()

    def claim(self, runner_id: str) -> dict[str, Any] | None:
        """Claim the next lease. A 204 (no work) returns ``None``."""
        response = self._post(f"{RUNNER_V1}/leases", {"runner_id": runner_id})
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def heartbeat(self, task_id: str, runner_id: str) -> dict[str, Any] | None:
        response = self._post(
            f"{RUNNER_V1}/leases/{task_id}/heartbeats",
            {"runner_id": runner_id},
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    def complete(self, task_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
        response = self._post(f"{RUNNER_V1}/leases/{task_id}/completions", evidence)
        response.raise_for_status()
        return response.json()

