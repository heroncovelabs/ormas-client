from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform as _platform
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from .config import get_secret, load, save, set_secret
from .http import OrmasClient
from .openhands_runner import CERTIFIED_TUPLES
from .runner import execute_lease, head_commit, repo_id_for


def _login(args: argparse.Namespace) -> int:
    if not args.access_key.startswith("tb_live_"):
        raise SystemExit("access key must start with tb_live_")
    if not args.openrouter_key.startswith("sk-or-"):
        raise SystemExit("OpenRouter key must start with sk-or-")
    # Both keys live ONLY in the OS keyring; the config file never stores them.
    set_secret("access-key", args.access_key)
    set_secret("openrouter-key", args.openrouter_key)
    config = load()
    config.control_url = args.control_url.rstrip("/")
    config.gateway_url = args.gateway_url.rstrip("/")
    save(config)
    print("Credentials saved in the operating-system keyring.")
    return 0


def _doctor(_: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Python", sys.version_info[:2] == (3, 12), sys.version.split()[0]))
    checks.append(("Git", shutil.which("git") is not None, shutil.which("git") or "missing"))
    try:
        version = importlib.metadata.version("openhands")
        checks.append(("OpenHands", version == "1.16.0", version))
    except importlib.metadata.PackageNotFoundError:
        checks.append(("OpenHands", False, "missing"))
    access_key = get_secret("access-key")
    openrouter_key = get_secret("openrouter-key")
    checks.append(("Ormas key", bool(access_key), "configured" if access_key else "missing"))
    checks.append(
        ("OpenRouter key", bool(openrouter_key), "configured" if openrouter_key else "missing")
    )
    if access_key:
        try:
            health = OrmasClient(load().gateway_url, access_key).health()
            checks.append(("Gateway", health.get("status") == "ok", str(health.get("status"))))
        except Exception as exc:  # network diagnostics belong in doctor output
            checks.append(("Gateway", False, type(exc).__name__))
    else:
        checks.append(("Gateway", False, "not checked"))
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL':4}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def _repo_add(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.is_dir() or not (path / ".git").exists():
        raise SystemExit(f"not a Git repository: {path}")
    subprocess.run(["git", "-C", str(path), "status", "--porcelain"], check=True, capture_output=True)
    config = load()
    # Record only the alias, local path and a stable derived repo_id locally.
    config.repositories[args.alias] = str(path)
    config.repo_ids[args.alias] = repo_id_for(path)
    save(config)
    print(f"Registered {args.alias} -> {path}")
    return 0


_INLINE_HOST_COMMANDS: dict[str, list[str]] = {
    "codex": ["codex", "mcp", "add", "ormas", "--", "ormas-mcp"],
    "claude": ["claude", "mcp", "add", "--scope", "project", "ormas", "--", "ormas-mcp"],
}


def _connect(args: argparse.Namespace) -> int:
    path = Path.cwd().resolve()
    alias = path.name
    if not path.is_dir() or not (path / ".git").exists():
        raise SystemExit(f"not a Git repository: {path}")
    subprocess.run(["git", "-C", str(path), "status", "--porcelain"], check=True, capture_output=True)
    config = load()
    config.repositories[alias] = str(path)
    config.repo_ids[alias] = repo_id_for(path)
    save(config)
    command = _INLINE_HOST_COMMANDS[args.host]
    subprocess.run(command, check=True, capture_output=True)
    print(f"Connected {alias} -> {path}. Restart {args.host} to load the ormas MCP server.")
    return 0


def _platform_tag() -> str:
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "wsl2"
    return "linux"


# Common field names the gateway routing preview may use for the selected
# certified tuple, checked at the top level and inside common nested
# envelopes (result/selection/preview).
_TUPLE_FIELDS = (
    "selected_tuple_id",
    "would_have_selected_tuple_id",
    "selected_tuple_key",
    "tuple_key",
    "tuple",
)


def _select_tuple(preview: dict[str, object]) -> str | None:
    """Parse the selected certified tuple out of a gateway routing preview.

    Checks common field names both at the top level and nested under
    ``result``/``selection``/``preview``. Returns ``None`` if no candidate is
    found so the caller can fail closed.
    """
    containers: list[dict[str, object]] = [preview]
    for nested_key in ("result", "selection", "preview"):
        nested = preview.get(nested_key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for field in _TUPLE_FIELDS:
            value = container.get(field)
            if value:
                return str(value)
    return None


def run_registered_task(
    *,
    repo_alias: str,
    brief: str,
    verify_command: str,
    allowed_paths: list[str],
    cost_cap_usd: float,
    dry_run: bool,
    tuple_override: str | None = None,
) -> dict[str, object]:
    """Runner-v1 orchestration shared by the CLI runner and the inline MCP tool.

    Loads the registered repo alias, requires keyring credentials, derives
    HEAD/base/repo id, registers the runner and repo with the portal control
    plane, keeps dry-run nonqueueing, creates/claims a runner-v1 task for
    live runs, requests a nonexecuting gateway routing preview (never
    sending the local repo path, source or OpenRouter key), enforces the
    certified tuple allowlist, executes locally in the detached worktree and
    returns a structured review result. Never auto-merges.
    """
    args = SimpleNamespace(
        repo_alias=repo_alias,
        brief=brief,
        verify_command=verify_command,
        allowed_path=allowed_paths,
        cost_cap=cost_cap_usd,
        dry_run=dry_run,
        tuple=tuple_override,
    )
    config = load()
    repo_path = config.repositories.get(args.repo_alias)
    if not repo_path:
        raise SystemExit(f"unknown repository alias: {args.repo_alias}")
    access_key = get_secret("access-key")
    if not access_key:
        raise SystemExit("not logged in; run `ormas runner login` first")
    repo = Path(repo_path)
    base_commit = head_commit(repo)
    repo_id = config.repo_ids.get(args.repo_alias) or repo_id_for(repo)

    # Runner-v1 lifecycle runs against the portal control plane only.
    control = OrmasClient(config.control_url, access_key)
    runner_id = config.runner_id or f"runner-{uuid.uuid4().hex[:12]}"
    control.register_runner(runner_id, _version(), _platform_tag(), 1)
    control.register_repo(repo_id, runner_id, args.repo_alias, base_commit)
    config.runner_id = runner_id
    save(config)

    task_id = f"task-{uuid.uuid4().hex[:12]}"

    if args.dry_run:
        # Dry-run is the default and must never create a task, claim (and
        # thereby strand) a lease, nor execute or edit anything. Only the
        # runner/repo registration above has run.
        return {"task_id": task_id, "status": "dry_run", "base_commit": base_commit}

    control.create_task(
        task_id=task_id,
        runner_id=runner_id,
        repo_id=repo_id,
        base_commit=base_commit,
        brief=args.brief,
        verify_command=args.verify_command,
        allowed_paths=list(args.allowed_path),
        budget_usd=args.cost_cap,
    )

    lease = control.claim(runner_id)
    if lease is None:
        return {"task_id": task_id, "status": "no_lease"}

    # The claim response is {lease_token, lease_expires_at, task: {...}}. The
    # plaintext lease_token is kept only in memory for the heartbeat/complete
    # calls below; it is never written to disk.
    lease_token = str(lease.get("lease_token") or "")
    task = lease.get("task")
    if not lease_token or not isinstance(task, dict):
        raise SystemExit("malformed claim response; refusing to execute")

    # Validate the task/base/repo envelope before doing any work.
    if str(task.get("task_id")) != task_id:
        raise SystemExit("claimed task_id does not match the created task; refusing to execute")
    if str(task.get("repo_id")) != repo_id:
        raise SystemExit("claimed repo_id does not match the registered repo; refusing to execute")
    if str(task.get("base_commit")) != base_commit:
        raise SystemExit("claimed base_commit does not match the local repo; refusing to execute")

    # Request a gateway routing preview WITHOUT sending the repo path, source,
    # OpenRouter key or any other local secret.
    preview = OrmasClient(config.gateway_url, access_key).routing_preview(
        {
            "task": args.brief,
            "task_type": "coding",
            "policy": "draft",
            "dry_run": True,
            "worker_enabled": False,
        }
    )
    tuple_id = _select_tuple(preview) or args.tuple
    if not tuple_id or tuple_id not in CERTIFIED_TUPLES:
        raise SystemExit(f"selected tuple '{tuple_id}' is not certified; refusing to execute")

    openrouter_key = get_secret("openrouter-key")
    if not openrouter_key:
        raise SystemExit("missing OpenRouter key; run `ormas runner login` first")

    return execute_lease(
        client=control,
        repo=repo,
        runner_id=runner_id,
        lease_token=lease_token,
        task=task,
        tuple_id=tuple_id,
        openrouter_key=openrouter_key,
        budget_usd=args.cost_cap,
    )


def _runner_start(args: argparse.Namespace) -> int:
    result = run_registered_task(
        repo_alias=args.repo_alias,
        brief=args.brief,
        verify_command=args.verify_command,
        allowed_paths=list(args.allowed_path),
        cost_cap_usd=args.cost_cap,
        dry_run=args.dry_run,
        tuple_override=args.tuple,
    )
    if "status" in result:
        # dry_run and no_lease results carry a structured "status" instead
        # of a completed execute_lease summary.
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(
        f"task={result['task_id']} tuple={result['tuple']} "
        f"worktree={result['worktree']} result_commit={result['result_commit']}"
    )
    return 0


def _version() -> str:
    try:
        return importlib.metadata.version("ormas-client")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ormas")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=_doctor)
    runner = sub.add_parser("runner")
    runner_sub = runner.add_subparsers(dest="runner_command", required=True)
    login = runner_sub.add_parser("login")
    login.add_argument("--access-key", required=True)
    login.add_argument("--openrouter-key", required=True)
    login.add_argument("--control-url", default="https://ormas.ai")
    login.add_argument("--gateway-url", default="https://ormas-gateway.fly.dev")
    login.set_defaults(func=_login)
    start = runner_sub.add_parser("start")
    start.add_argument("--repo-alias", required=True)
    start.add_argument("--brief", required=True)
    start.add_argument("--cost-cap", type=float, default=0.25)
    start.add_argument("--verify-command", default="pytest -q")
    start.add_argument(
        "--allowed-path",
        action="append",
        default=["src/**", "tests/**"],
        help="glob of paths the produced diff may touch (repeatable)",
    )
    start.add_argument("--tuple", default=None, help=argparse.SUPPRESS)
    start.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    start.set_defaults(func=_runner_start)
    repo = sub.add_parser("repo")
    repo_sub = repo.add_subparsers(dest="repo_command", required=True)
    add = repo_sub.add_parser("add")
    add.add_argument("alias")
    add.add_argument("path")
    add.set_defaults(func=_repo_add)
    connect = sub.add_parser("connect")
    connect.add_argument("--host", choices=sorted(_INLINE_HOST_COMMANDS), required=True)
    connect.set_defaults(func=_connect)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
