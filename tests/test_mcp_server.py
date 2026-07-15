from __future__ import annotations

from ormas_client import mcp_server


def test_inline_submit_delegates_to_local_runner_v1_service(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_registered_task(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {
            "task_id": "task-1",
            "status": "completed",
            "worktree": "/tmp/review-only",
            "result_commit": "abc1234",
        }

    monkeypatch.setattr(mcp_server, "run_registered_task", fake_run_registered_task)

    result = mcp_server.ormas_submit_task(
        task="Repair the parser",
        repo_alias="customer-repo",
        verify_command="pytest -q tests/test_parser.py",
        allowed_paths=["src/parser.py", "tests/test_parser.py"],
        cost_cap_usd=0.25,
        dry_run=False,
    )

    assert result["status"] == "completed"
    assert seen == {
        "repo_alias": "customer-repo",
        "brief": "Repair the parser",
        "verify_command": "pytest -q tests/test_parser.py",
        "allowed_paths": ["src/parser.py", "tests/test_parser.py"],
        "cost_cap_usd": 0.25,
        "dry_run": False,
    }


def test_inline_mcp_never_uses_legacy_gateway_repo_path_submission() -> None:
    source = mcp_server.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert "repo_path" not in text
    assert ".submit(" not in text
