from __future__ import annotations

from pathlib import Path

import httpx

from ormas_client.http import OrmasClient


def test_control_plane_transport_uses_runner_v1_paths_and_handles_empty_claim() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content or b"{}")
        seen.append((request.url.path, body))
        if request.url.path.endswith("/leases"):
            return httpx.Response(204)
        return httpx.Response(200, json={"schema_version": "ormas-runner-v1"})

    transport = httpx.MockTransport(handler)
    client = OrmasClient(
        "https://ormas.ai",
        "tb_live_example",
        transport=transport,
    )

    assert client.register_runner("runner-1", "0.1.0", "macos", 1)["schema_version"] == "ormas-runner-v1"
    assert client.register_repo("repo-1", "runner-1", "trading", "a1b2c3d")["schema_version"] == "ormas-runner-v1"
    assert client.create_task(
        task_id="task-1",
        runner_id="runner-1",
        repo_id="repo-1",
        base_commit="a1b2c3d",
        brief="Repair parser edge case",
        verify_command="pytest -q",
        allowed_paths=["src/**", "tests/**"],
        budget_usd=0.25,
    )["schema_version"] == "ormas-runner-v1"
    assert client.claim("runner-1") is None
    assert [path for path, _ in seen] == [
        "/api/runner/v1/registrations",
        "/api/runner/v1/repositories",
        "/api/runner/v1/tasks",
        "/api/runner/v1/leases",
    ]
    assert all("repo_path" not in body and "provider_key" not in body for _, body in seen)


def test_public_package_contains_local_openhands_executor() -> None:
    package = Path(__file__).parents[1] / "src" / "ormas_client"
    assert (package / "runner.py").is_file()
    assert (package / "openhands_runner.py").is_file()
