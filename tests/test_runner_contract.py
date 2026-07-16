from __future__ import annotations

from pathlib import Path
import subprocess

import httpx
import pytest

from ormas_client.http import OrmasClient
from ormas_client import cli
from ormas_client.cli import _select_tuple
from ormas_client.runner import execute_lease


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


@pytest.mark.parametrize(
    ("status_code", "body", "expected"),
    [
        (
            400,
            {
                "error": "invalid task draft",
                "issues": [{"path": ["verify_command"], "message": "Invalid string"}],
            },
            "invalid task draft: verify_command: Invalid string",
        ),
        (500, {"error": "runner schema is not ready"}, "runner schema is not ready"),
    ],
)
def test_runner_api_errors_surface_safe_actionable_server_detail(
    status_code: int,
    body: dict[str, object],
    expected: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    client = OrmasClient(
        "https://ormas.ai",
        "tb_live_must_not_leak",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RuntimeError) as exc_info:
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

    message = str(exc_info.value)
    assert expected in message
    assert f"HTTP {status_code}" in message
    assert "tb_live_must_not_leak" not in message
    assert "Traceback" not in message


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


def test_gateway_worker_preview_shape_selects_the_would_run_tuple() -> None:
    assert _select_tuple(
        {"worker_preview": {"would_run_tuple_id": "grok45-openrouter-oh"}}
    ) == "grok45-openrouter-oh"


def test_preexecution_preview_failure_completes_the_claimed_lease(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    config = type("Config", (), {
        "repositories": {"repo": str(repo)},
        "repo_ids": {"repo": "repo-1"},
        "runner_id": "runner-1",
        "control_url": "https://ormas.ai",
        "gateway_url": "https://ormas-gateway.fly.dev",
    })()
    completed: list[tuple[str, str, str, dict[str, object]]] = []

    class Client:
        def __init__(self, base_url: str, _key: str):
            self.base_url = base_url

        def register_runner(self, *_args):
            return {}

        def register_repo(self, *_args):
            return {}

        def create_task(self, **_kwargs):
            return {}

        def claim(self, _runner_id: str):
            return {
                "lease_token": "lease-secret",
                "task": {
                    "task_id": "task-fixed",
                    "repo_id": "repo-1",
                    "base_commit": "abc1234",
                },
            }

        def routing_preview(self, _payload):
            raise RuntimeError("HTTP 404: Not Found")

        def complete(self, task_id, runner_id, token, evidence):
            completed.append((task_id, runner_id, token, evidence))
            return {}

    monkeypatch.setattr(cli, "load", lambda: config)
    monkeypatch.setattr(cli, "save", lambda _config: None)
    monkeypatch.setattr(cli, "get_secret", lambda _name: "configured")
    monkeypatch.setattr(cli, "head_commit", lambda _repo: "abc1234")
    monkeypatch.setattr(cli, "OrmasClient", Client)
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: type("U", (), {"hex": "fixed"})())

    with pytest.raises(RuntimeError, match="routing preview.*HTTP 404"):
        cli.run_registered_task(
            repo_alias="repo",
            brief="repair parser",
            verify_command="pytest -q",
            allowed_paths=["src/parser.py"],
            cost_cap_usd=0.25,
            dry_run=False,
        )

    assert completed == [(
        "task-fixed",
        "runner-1",
        "lease-secret",
        {
            "status": "failed",
            "verification_passed": False,
            "patch_non_empty": False,
            "base_commit": "abc1234",
            "error_category": "routing_preview",
        },
    )]


def test_execute_lease_edits_only_a_detached_worktree_and_reports_strict_evidence(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "value.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "base"],
        check=True,
    )
    base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    def fake_openhands(**kwargs: object) -> str:
        worktree = kwargs["worktree"]
        assert isinstance(worktree, Path)
        (worktree / "value.txt").write_text("after\n", encoding="utf-8")
        return "done"

    monkeypatch.setattr("ormas_client.runner.run_openhands", fake_openhands)

    class Client:
        completed: dict[str, object] | None = None

        def heartbeat(self, *_: object) -> dict[str, object]:
            return {}

        def complete(self, task_id: str, runner_id: str, token: str, evidence: dict[str, object]) -> dict[str, object]:
            assert (task_id, runner_id, token) == ("task-1", "runner-1", "lease_secret")
            self.completed = evidence
            return {}

    client = Client()
    result = execute_lease(
        client=client,  # type: ignore[arg-type]
        repo=repo,
        runner_id="runner-1",
        lease_token="lease_secret",
        task={
            "task_id": "task-1",
            "base_commit": base,
            "brief": "change value",
            "verify_command": "python -c \"assert __import__('pathlib').Path('value.txt').read_text() == 'after\\n'\"",
            "allowed_paths": ["value.txt"],
        },
        tuple_id="glm52-openrouter-oh",
        openrouter_key="sk-or-local-only",
        budget_usd=0.25,
    )
    assert (repo / "value.txt").read_text(encoding="utf-8") == "before\n"
    assert Path(result["worktree"]).joinpath("value.txt").read_text(encoding="utf-8") == "after\n"
    assert client.completed == {
        "status": "completed",
        "verification_passed": True,
        "patch_non_empty": True,
        "base_commit": base,
        "result_commit": result["result_commit"],
    }
