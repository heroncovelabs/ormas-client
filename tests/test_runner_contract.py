from __future__ import annotations

from pathlib import Path

import httpx

from ormas_client.http import OrmasClient
from ormas_client.cli import _select_tuple


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


def test_control_plane_payloads_match_the_deployed_runner_v1_contract() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content or b"{}")
        seen.append((request.url.path, body))
        return httpx.Response(200, json={"schema_version": "ormas-runner-v1"})

    client = OrmasClient(
        "https://ormas.ai",
        "tb_live_example",
        transport=httpx.MockTransport(handler),
    )
    client.register_runner("runner-1", "0.1.0", "macos", 1)
    client.register_repo("repo-1", "runner-1", "trading", "a1b2c3d")
    client.create_task(
        task_id="task-1",
        runner_id="runner-1",
        repo_id="repo-1",
        base_commit="a1b2c3d",
        brief="Repair parser edge case",
        verify_command="pytest -q",
        allowed_paths=["src/**"],
        budget_usd=0.25,
    )
    client.claim("runner-1")
    client.heartbeat("task-1", "runner-1", "lease_secret")
    client.complete(
        "task-1",
        "runner-1",
        "lease_secret",
        {
            "status": "completed",
            "verification_passed": True,
            "patch_non_empty": True,
            "base_commit": "a1b2c3d",
            "result_commit": "f9e8d7c",
        },
    )

    assert seen == [
        (
            "/api/runner/v1/registrations",
            {
                "schema_version": "ormas-runner-v1",
                "runner_id": "runner-1",
                "runner_version": "0.1.0",
                "platform": "macos",
                "capacity": 1,
                "health": {"status": "healthy", "active_tasks": 0},
            },
        ),
        (
            "/api/runner/v1/repositories",
            {
                "schema_version": "ormas-runner-v1",
                "repo_id": "repo-1",
                "runner_id": "runner-1",
                "display_alias": "trading",
                "base_commit": "a1b2c3d",
                "preflight_state": "ready",
            },
        ),
        (
            "/api/runner/v1/tasks",
            {
                "schema_version": "ormas-runner-v1",
                "task_id": "task-1",
                "runner_id": "runner-1",
                "repo_id": "repo-1",
                "base_commit": "a1b2c3d",
                "brief": "Repair parser edge case",
                "verify_command": "pytest -q",
                "allowed_paths": ["src/**"],
                "budget_usd": 0.25,
            },
        ),
        ("/api/runner/v1/leases", {"schema_version": "ormas-runner-v1", "runner_id": "runner-1"}),
        (
            "/api/runner/v1/leases/task-1/heartbeat",
            {
                "schema_version": "ormas-runner-v1",
                "runner_id": "runner-1",
                "lease_token": "lease_secret",
            },
        ),
        (
            "/api/runner/v1/leases/task-1/complete",
            {
                "schema_version": "ormas-runner-v1",
                "runner_id": "runner-1",
                "lease_token": "lease_secret",
                "evidence": {
                    "status": "completed",
                    "verification_passed": True,
                    "patch_non_empty": True,
                    "base_commit": "a1b2c3d",
                    "result_commit": "f9e8d7c",
                },
            },
        ),
    ]


def test_public_package_contains_local_openhands_executor() -> None:
    package = Path(__file__).parents[1] / "src" / "ormas_client"
    assert (package / "runner.py").is_file()
    assert (package / "openhands_runner.py").is_file()
    source = (package / "openhands_runner.py").read_text(encoding="utf-8")
    assert "from openhands.sdk import" in source
    assert "from openhands.tools.preset.default import get_default_tools" in source


def test_dry_run_does_not_queue_work_and_gateway_preview_is_nonexecuting() -> None:
    package = Path(__file__).parents[1] / "src" / "ormas_client"
    source = (package / "cli.py").read_text(encoding="utf-8")
    dry_run_gate = source.index("if args.dry_run:")
    create_task = source.index("control.create_task(")
    assert dry_run_gate < create_task
    assert '"dry_run": True' in source
    assert '"worker_enabled": False' in source
    assert _select_tuple({"result": {"selected_tuple_id": "glm52-openrouter-oh"}}) == "glm52-openrouter-oh"
