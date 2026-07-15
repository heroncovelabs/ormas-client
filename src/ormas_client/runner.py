"""Local runner orchestration for the public Ormas beta.

Coordinates the disposable, detached git worktree, allowed-path and verify
gates, lease heartbeats and the strict sanitized completion evidence. Nothing
here writes to the registered checkout or auto-merges the produced patch.
"""

from __future__ import annotations

import fnmatch
import shlex
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from .http import OrmasClient
from .openhands_runner import run_openhands


def git(repo: Path, *args: str) -> str:
    """Run a git command without any shell interpretation and return stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def head_commit(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def repo_id_for(path: Path) -> str:
    """Derive a stable repo_id from the absolute local path."""
    return "repo-" + uuid.uuid5(uuid.NAMESPACE_URL, path.resolve().as_uri()).hex[:16]


def create_worktree(repo: Path, base_commit: str) -> Path:
    """Create a disposable detached worktree at ``base_commit``."""
    target = Path(tempfile.mkdtemp(prefix="ormas-worktree-"))
    # --detach keeps the registered checkout's branches untouched.
    git(repo, "worktree", "add", "--detach", str(target), base_commit)
    return target


def remove_worktree(repo: Path, worktree: Path) -> None:
    try:
        git(repo, "worktree", "remove", "--force", str(worktree))
    except subprocess.CalledProcessError:
        pass


def changed_paths(worktree: Path) -> list[str]:
    """Return paths touched in the worktree relative to its base commit."""
    out = git(worktree, "status", "--porcelain")
    paths: list[str] = []
    for line in out.splitlines():
        entry = line[3:].strip()
        if " -> " in entry:  # rename
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.append(entry)
    return paths


def enforce_allowed_paths(worktree: Path, allowed: list[str]) -> None:
    """Fail closed if the diff touches a path outside the allow-list."""
    for path in changed_paths(worktree):
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed):
            raise ValueError(f"change touches disallowed path: {path}")


def run_verify(worktree: Path, verify_command: str) -> subprocess.CompletedProcess[str]:
    """Execute the verify command with no shell metacharacter interpretation."""
    argv = shlex.split(verify_command)
    if not argv:
        raise ValueError("empty verify command")
    return subprocess.run(argv, cwd=str(worktree), capture_output=True, text=True)


def commit_patch(worktree: Path, task_id: str) -> str:
    git(worktree, "add", "-A")
    git(worktree, "commit", "-m", f"ormas task {task_id}")
    return head_commit(worktree)


class Heartbeat:
    """Background lease heartbeat kept alive during execution."""

    def __init__(self, client: OrmasClient, task_id: str, runner_id: str, interval: float = 15.0):
        self._client = client
        self._task_id = task_id
        self._runner_id = runner_id
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._client.heartbeat(self._task_id, self._runner_id)
            except Exception:  # a transient heartbeat failure must not crash work
                pass

    def __enter__(self) -> Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)


def execute_lease(
    *,
    client: OrmasClient,
    repo: Path,
    lease: dict[str, Any],
    tuple_id: str,
    openrouter_key: str,
    budget_usd: float,
) -> dict[str, Any]:
    """Execute a leased task end to end and return sanitized evidence.

    The registered checkout is never modified; all work happens in a disposable
    detached worktree that is removed afterwards.
    """
    task_id = str(lease.get("task_id") or lease.get("id"))
    runner_id = str(lease.get("runner_id"))
    base_commit = str(lease.get("base_commit") or head_commit(repo))
    brief = str(lease.get("brief") or "")
    verify_command = str(lease.get("verify_command") or "")
    allowed = list(lease.get("allowed_paths") or [])

    worktree = create_worktree(repo, base_commit)
    try:
        with Heartbeat(client, task_id, runner_id):
            run_openhands(
                tuple_id=tuple_id,
                openrouter_key=openrouter_key,
                worktree=worktree,
                brief=brief,
                budget_usd=budget_usd,
            )
        # Gates precede completion: allowed paths, then verify, then commit.
        touched = changed_paths(worktree)
        if not touched:
            raise ValueError("no changes produced")
        enforce_allowed_paths(worktree, allowed)
        verify = run_verify(worktree, verify_command)
        if verify.returncode != 0:
            raise ValueError(f"verify command failed with exit {verify.returncode}")
        result_commit = commit_patch(worktree, task_id)
        evidence = {
            "task_id": task_id,
            "status": "completed",
            "tuple": tuple_id,
            "base_commit": base_commit,
            "result_commit": result_commit,
            "changed_paths": touched,
            "verify_exit_code": verify.returncode,
        }
        client.complete(task_id, evidence)
        return {
            "task_id": task_id,
            "tuple": tuple_id,
            "worktree": str(worktree),
            "result_commit": result_commit,
        }
    except Exception as exc:
        # On any failure send sanitized failed evidence for the existing lease.
        try:
            client.complete(
                task_id,
                {"task_id": task_id, "status": "failed", "reason": type(exc).__name__},
            )
        except Exception:
            pass
        raise
