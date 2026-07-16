from __future__ import annotations

from typing import Any

import httpx

# Strict runner-v1 control-plane API surface. These are the only paths the
# public runner is permitted to call on the portal control plane.
RUNNER_V1 = "/api/runner/v1"

# Every runner-v1 body carries the schema version the deployed portal expects.
SCHEMA_VERSION = "ormas-runner-v1"

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

    @staticmethod
    def _format_issue(issue: dict[str, Any]) -> str:
        """Render one validation issue as ``<dot.path>: <message>``."""
        message = issue.get("message")
        if not isinstance(message, str) or not message:
            return ""
        path = issue.get("path")
        if isinstance(path, list) and path:
            return f"{'.'.join(str(p) for p in path)}: {message}"
        if isinstance(path, str) and path:
            return f"{path}: {message}"
        return message

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Raise a safe ``RuntimeError`` for non-success responses.

        The message starts with ``HTTP <status>`` and surfaces only the
        server's top-level ``error`` string plus concise ``issues`` details.
        No authorization headers, access keys, request bodies, raw source, or
        local paths ever appear in the message. JSON is parsed best-effort; a
        non-JSON or malformed body yields a bounded status/reason message.
        """
        status = response.status_code
        if status < 400:
            return

        data: Any = None
        try:
            data = response.json()
        except Exception:
            data = None

        parts: list[str] = []
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, str) and error:
                parts.append(error)
            issues = data.get("issues")
            if isinstance(issues, list) and issues:
                details = [
                    detail
                    for issue in issues
                    if isinstance(issue, dict)
                    for detail in (self._format_issue(issue),)
                    if detail
                ]
                if details:
                    parts.append("; ".join(details))

        if not parts:
            reason = response.reason_phrase or "error"
            parts.append(reason)

        raise RuntimeError(f"HTTP {status}: " + ": ".join(parts))

    # -- gateway ------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        response = self._client.get("/health")
        self._raise_for_status(response)
        return response.json()

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post("/ormas/task", payload)
        self._raise_for_status(response)
        return response.json()

    def routing_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask the gateway to route a brief WITHOUT sending any local secret."""
        response = self._post("/ormas/task", payload)
        self._raise_for_status(response)
        return response.json()

    # -- runner-v1 control plane -------------------------------------------
    def register_runner(
        self, runner_id: str, version: str, platform: str, capacity: int
    ) -> dict[str, Any]:
        response = self._post(
            f"{RUNNER_V1}/registrations",
            {
                "schema_version": SCHEMA_VERSION,
                "runner_id": runner_id,
                "runner_version": version,
                "platform": platform,
                "capacity": capacity,
                "health": {"status": "healthy", "active_tasks": 0},
            },
        )
        self._raise_for_status(response)
        return response.json()

    def register_repo(
        self, repo_id: str, runner_id: str, alias: str, head_commit: str
    ) -> dict[str, Any]:
        response = self._post(
            f"{RUNNER_V1}/repositories",
            {
                "schema_version": SCHEMA_VERSION,
                "repo_id": repo_id,
                "runner_id": runner_id,
                "display_alias": alias,
                "base_commit": head_commit,
                "preflight_state": "ready",
            },
        )
        self._raise_for_status(response)
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
                "schema_version": SCHEMA_VERSION,
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
        self._raise_for_status(response)
        return response.json()

    def claim(self, runner_id: str) -> dict[str, Any] | None:
        """Claim the next lease. A 204 (no work) returns ``None``.

        On success the response is ``{lease_token, lease_expires_at,
        task: {...}}``. The plaintext ``lease_token`` must only ever be kept
        in memory and passed back on subsequent heartbeat/complete calls.
        """
        response = self._post(
            f"{RUNNER_V1}/leases", {"schema_version": SCHEMA_VERSION, "runner_id": runner_id}
        )
        if response.status_code == 204:
            return None
        self._raise_for_status(response)
        return response.json()

    def heartbeat(self, task_id: str, runner_id: str, lease_token: str) -> dict[str, Any] | None:
        response = self._post(
            f"{RUNNER_V1}/leases/{task_id}/heartbeat",
            {
                "schema_version": SCHEMA_VERSION,
                "runner_id": runner_id,
                "lease_token": lease_token,
            },
        )
        if response.status_code == 204:
            return None
        self._raise_for_status(response)
        return response.json()

    def complete(
        self,
        task_id: str,
        runner_id: str,
        lease_token: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._post(
            f"{RUNNER_V1}/leases/{task_id}/complete",
            {
                "schema_version": SCHEMA_VERSION,
                "runner_id": runner_id,
                "lease_token": lease_token,
                "evidence": evidence,
            },
        )
        self._raise_for_status(response)
        return response.json()

