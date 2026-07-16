"""Local runner orchestration for the public Ormas beta.

Coordinates the disposable, detached git worktree, allowed-path and verify
gates, lease heartbeats and the strict sanitized completion evidence. Nothing
here writes to the registered checkout or auto-merges the produced patch.
"""

from __future__ import annotations

import fnmatch
import math
import os
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
    tracked = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", "-z", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    return [
        raw.decode("utf-8", errors="surrogateescape")
        for raw in (tracked + untracked).split(b"\0")
        if raw
    ]


def _reported_openhands_cost(result: object) -> float | None:
    """Read a finite observed cost without treating a budget as usage."""
    if isinstance(result, dict):
        value = result.get("observed_cost_usd")
    else:
        value = getattr(result, "observed_cost_usd", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def enforce_allowed_paths(worktree: Path, allowed: list[str]) -> None:
    """Fail closed if the diff touches a path outside the allow-list."""
    for path in changed_paths(worktree):
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed):
            raise ValueError(f"change touches disallowed path: {path}")


def run_verify(
    worktree: Path,
    verify_command: str,
    *,
    dependency_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the verify command with no shell metacharacter interpretation."""
    argv = shlex.split(verify_command)
    if not argv:
        raise ValueError("empty verify command")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(worktree)
    if dependency_root is not None:
        venv_bin = dependency_root / ".venv" / "bin"
        if venv_bin.is_dir():
            env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(argv, cwd=str(worktree), env=env, capture_output=True, text=True)


# Explicit local git identity so a fresh, unconfigured client machine (no
# ~/.gitconfig user.name/user.email) can still commit the produced patch.
COMMIT_AUTHOR_NAME = "Ormas Client Runner"
COMMIT_AUTHOR_EMAIL = "runner@ormas.ai"


def commit_patch(worktree: Path, task_id: str) -> str:
    git(worktree, "add", "-A")
    git(
        worktree,
        "-c",
        f"user.name={COMMIT_AUTHOR_NAME}",
        "-c",
        f"user.email={COMMIT_AUTHOR_EMAIL}",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        f"ormas task {task_id}",
    )
    return head_commit(worktree)


class RunnerFailure(RuntimeError):
    """A failure raised with the permitted error_category to report to the portal."""

    def __init__(self, message: str, error_category: str = "unknown") -> None:
        super().__init__(message)
        self.error_category = error_category


class Heartbeat:
    """Background lease heartbeat kept alive during execution.

    The plaintext ``lease_token`` returned by ``claim`` is carried only in
    memory for the lifetime of the lease; it is never written to disk.
    """

    def __init__(
        self,
        client: OrmasClient,
        task_id: str,
        runner_id: str,
        lease_token: str,
        interval: float = 15.0,
    ):
        self._client = client
        self._task_id = task_id
        self._runner_id = runner_id
        self._lease_token = lease_token
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._client.heartbeat(self._task_id, self._runner_id, self._lease_token)
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
    runner_id: str,
    lease_token: str,
    task: dict[str, Any],
    tuple_id: str,
    openrouter_key: str,
    budget_usd: float,
) -> dict[str, Any]:
    """Execute a leased task end to end and post strict sanitized evidence.

    ``task`` is the ``task`` envelope nested in the ``claim`` response. The
    registered checkout is never modified; all work happens in a disposable
    detached worktree that is kept afterwards for client review. The
    plaintext ``lease_token`` is passed in and used only in memory for the
    heartbeat and the terminal completion; it is never persisted locally.
    """
    task_id = str(task["task_id"])
    base_commit = str(task.get("base_commit") or head_commit(repo))
    brief = str(task.get("brief") or "")
    verify_command = str(task.get("verify_command") or "")
    allowed = list(task.get("allowed_paths") or [])

    worktree = create_worktree(repo, base_commit)
    try:
        with Heartbeat(client, task_id, runner_id, lease_token):
            openhands_result = run_openhands(
                tuple_id=tuple_id,
                openrouter_key=openrouter_key,
                worktree=worktree,
                brief=brief,
                budget_usd=budget_usd,
            )
        # Gates precede completion: patch present, allowed paths, then verify.
        touched = changed_paths(worktree)
        if not touched:
            raise RunnerFailure("no changes produced", "verification_failed")
        try:
            enforce_allowed_paths(worktree, allowed)
        except ValueError as exc:
            raise RunnerFailure(str(exc), "verification_failed") from exc
        verify = run_verify(worktree, verify_command, dependency_root=repo)
        if verify.returncode != 0:
            raise RunnerFailure(
                f"verify command failed with exit {verify.returncode}", "verification_failed"
            )
        result_commit = commit_patch(worktree, task_id)
        evidence = {
            "status": "completed",
            "verification_passed": True,
            "patch_non_empty": True,
            "base_commit": base_commit,
            "result_commit": result_commit,
        }
        client.complete(task_id, runner_id, lease_token, evidence)
        result: dict[str, Any] = {
            "task_id": task_id,
            "tuple": tuple_id,
            "worktree": str(worktree),
            "result_commit": result_commit,
            "changed_files": touched,
        }
        observed_cost = _reported_openhands_cost(openhands_result)
        if observed_cost is not None:
            result["observed_cost_usd"] = observed_cost
        return result
    except Exception as exc:
        # On any failure send sanitized failed evidence for the existing lease.
        category = exc.error_category if isinstance(exc, RunnerFailure) else "unknown"
        try:
            client.complete(
                task_id,
                runner_id,
                lease_token,
                {
                    "status": "failed",
                    "verification_passed": False,
                    "patch_non_empty": False,
                    "base_commit": base_commit,
                    "error_category": category,
                },
            )
        except Exception:
            pass
        raise
