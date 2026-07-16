from __future__ import annotations

from ormas_client import mcp_server


def test_inline_submit_delegates_to_local_runner_v1_service(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_registered_task(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {
            "task_id": "task-1",
            "status": "dry_run",
            "base_commit": "abc1234",
        }

    monkeypatch.setattr(mcp_server, "run_registered_task", fake_run_registered_task)

    result = mcp_server.ormas_submit_task(
        task="Repair the parser",
        repo_alias="customer-repo",
        verify_command="pytest -q tests/test_parser.py",
        allowed_paths=["src/parser.py", "tests/test_parser.py"],
        cost_cap_usd=0.25,
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert seen == {
        "repo_alias": "customer-repo",
        "brief": "Repair the parser",
        "verify_command": "pytest -q tests/test_parser.py",
        "allowed_paths": ["src/parser.py", "tests/test_parser.py"],
        "cost_cap_usd": 0.25,
        "dry_run": True,
    }


def test_inline_mcp_never_uses_legacy_gateway_repo_path_submission() -> None:
    source = mcp_server.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert "repo_path" not in text
    assert ".submit(" not in text


def test_live_inline_submit_returns_a_pollable_job_without_running_worker_inline(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fail_if_run_inline(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("live worker must not execute inside the MCP request")

    def fake_launch(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {"job_id": "job-1", "task_id": "task-pending", "status": "queued"}

    monkeypatch.setattr(mcp_server, "run_registered_task", fail_if_run_inline)
    monkeypatch.setattr(mcp_server, "launch_background_job", fake_launch, raising=False)

    result = mcp_server.ormas_submit_task(
        task="Repair the parser",
        repo_alias="customer-repo",
        verify_command="pytest -q tests/test_parser.py",
        allowed_paths=["src/parser.py", "tests/test_parser.py"],
        cost_cap_usd=0.25,
        dry_run=False,
    )

    assert result == {"job_id": "job-1", "task_id": "task-pending", "status": "queued"}
    assert seen["repo_alias"] == "customer-repo"
    assert seen["dry_run"] is False


def test_inline_job_status_returns_terminal_review_evidence(monkeypatch) -> None:
    expected = {
        "job_id": "job-1",
        "task_id": "task-1",
        "status": "completed",
        "tuple": "grok45-openrouter-oh",
        "harness": "openhands",
        "verification_passed": True,
        "changed_files": ["src/parser.py"],
        "result_commit": "abc1234",
        "observed_cost_usd": 0.19,
    }
    monkeypatch.setattr(mcp_server, "read_background_job", lambda job_id: expected, raising=False)

    status_tool = getattr(mcp_server, "ormas_task_status", None)
    assert status_tool is not None, "MCP must expose a status/result polling tool"
    assert status_tool("job-1") == expected
