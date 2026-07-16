from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .cli import run_registered_task
from .config import config_path
from .runner import changed_paths

mcp = FastMCP("ormas")

_JOB_ID = re.compile(r"job-[A-Za-z0-9_-]{1,127}\Z")
_TASK_ID = re.compile(r"task-[A-Za-z0-9_-]{1,127}\Z")


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
        raise ValueError("invalid job_id")
    return job_id


def _validate_task_id(task_id: str) -> str:
    if not isinstance(task_id, str) or _TASK_ID.fullmatch(task_id) is None:
        raise ValueError("invalid task_id")
    return task_id


def _job_state_dir() -> Path:
    return config_path().parent / "state" / "jobs"


def _job_state_path(job_id: str) -> Path:
    job_id = _validate_job_id(job_id)
    directory = _job_state_dir()
    path = directory / f"{job_id}.json"
    if path.parent != directory or path.name != f"{job_id}.json":
        raise ValueError("invalid job_id")
    return path


def _write_job_state(state: dict[str, object]) -> None:
    job_id = _validate_job_id(str(state.get("job_id") or ""))
    directory = _job_state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = _job_state_path(job_id)
    fd, temporary = tempfile.mkstemp(prefix=f".{job_id}-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_background_job(job_id: str) -> dict[str, object]:
    path = _job_state_path(job_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"job_id": job_id, "status": "unknown"}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def launch_background_job(
    *,
    repo_alias: str,
    brief: str,
    verify_command: str,
    allowed_paths: list[str],
    cost_cap_usd: float,
    dry_run: bool,
) -> dict[str, object]:
    if dry_run:
        raise ValueError("background jobs require dry_run=False")

    job_id = _new_id("job")
    task_id = _new_id("task")
    _write_job_state({"job_id": job_id, "task_id": task_id, "status": "queued"})
    payload = {
        "job_id": job_id,
        "task_id": task_id,
        "repo_alias": repo_alias,
        "brief": brief,
        "verify_command": verify_command,
        "allowed_paths": list(allowed_paths),
        "cost_cap_usd": cost_cap_usd,
    }
    try:
        child = subprocess.Popen(
            [sys.executable, "-m", "ormas_client.cli", "background-job", "--job-id", job_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        if child.stdin is None:
            raise RuntimeError("background worker stdin unavailable")
        child.stdin.write(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        child.stdin.close()
    except Exception:
        _write_job_state(
            {
                "job_id": job_id,
                "task_id": task_id,
                "status": "failed",
                "error_category": "launch",
                "message": "background worker could not be started",
            }
        )
        raise
    return {"job_id": job_id, "task_id": task_id, "status": "queued"}


def _safe_changed_files(result: dict[str, Any]) -> list[str] | None:
    existing = result.get("changed_files")
    if isinstance(existing, list) and all(isinstance(path, str) for path in existing):
        return list(existing)
    worktree = result.get("worktree")
    if not isinstance(worktree, str) or not worktree:
        return None
    try:
        return changed_paths(Path(worktree))
    except Exception:
        return None


def _safe_result(result: dict[str, Any], job_id: str, task_id: str) -> dict[str, object]:
    evidence: dict[str, object] = {
        "job_id": job_id,
        "task_id": str(result.get("task_id") or task_id),
        "status": "completed",
        "harness": "openhands",
        "verification_passed": True,
    }
    for field in ("tuple", "worktree", "result_commit"):
        value = result.get(field)
        if isinstance(value, str) and value:
            evidence[field] = value
    changed = _safe_changed_files(result)
    if changed is not None:
        evidence["changed_files"] = changed
    observed_cost = _reported_cost(result)
    if observed_cost is not None:
        evidence["observed_cost_usd"] = observed_cost
    return evidence


def _reported_cost(result: dict[str, Any]) -> float | None:
    value = result.get("observed_cost_usd")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


_FAILURE_MESSAGES = {
    "configuration": "background task configuration failed",
    "launch": "background worker could not be started",
    "no_lease": "no task lease was available",
    "routing_preview": "routing preview failed",
    "verification_failed": "verification failed",
    "unknown": "background task failed",
}


def _failure_state(job_id: str, task_id: str, category: str) -> dict[str, object]:
    safe_category = category if category in _FAILURE_MESSAGES else "unknown"
    return {
        "job_id": job_id,
        "task_id": task_id,
        "status": "failed",
        "error_category": safe_category,
        "message": _FAILURE_MESSAGES[safe_category],
    }


def run_background_job(payload: dict[str, Any]) -> None:
    """Run one detached job and write only review-safe terminal evidence."""
    job_id = _validate_job_id(str(payload.get("job_id") or ""))
    task_id = _validate_task_id(str(payload.get("task_id") or ""))
    _write_job_state({"job_id": job_id, "task_id": task_id, "status": "running"})
    try:
        result = run_registered_task(
            repo_alias=str(payload["repo_alias"]),
            brief=str(payload["brief"]),
            verify_command=str(payload["verify_command"]),
            allowed_paths=list(payload["allowed_paths"]),
            cost_cap_usd=float(payload["cost_cap_usd"]),
            dry_run=False,
            task_id_override=task_id,
        )
        if result.get("status"):
            _write_job_state(_failure_state(job_id, task_id, str(result["status"])))
            return
        _write_job_state(_safe_result(result, job_id, task_id))
    except SystemExit:
        _write_job_state(_failure_state(job_id, task_id, "configuration"))
    except Exception as exc:
        category = getattr(exc, "error_category", "unknown")
        _write_job_state(_failure_state(job_id, task_id, str(category)))


@mcp.tool()
def ormas_submit_task(
    task: str,
    repo_alias: str,
    verify_command: str,
    allowed_paths: list[str],
    cost_cap_usd: float = 0.25,
    dry_run: bool = True,
) -> dict:
    # Delegates to the SAME runner-v1 service the CLI's `ormas runner start`
    # uses, so the lifecycle (registration, dry-run nonqueueing, task
    # create/claim, nonexecuting gateway preview, certified tuple allowlist,
    # local detached-worktree execution) is defined in exactly one place.
    if dry_run:
        return run_registered_task(
            repo_alias=repo_alias,
            brief=task,
            verify_command=verify_command,
            allowed_paths=allowed_paths,
            cost_cap_usd=cost_cap_usd,
            dry_run=True,
        )
    return launch_background_job(
        repo_alias=repo_alias,
        brief=task,
        verify_command=verify_command,
        allowed_paths=allowed_paths,
        cost_cap_usd=cost_cap_usd,
        dry_run=False,
    )


@mcp.tool()
def ormas_task_status(job_id: str) -> dict[str, object]:
    return read_background_job(_validate_job_id(job_id))


def main() -> None:
    mcp.run(transport="stdio")

