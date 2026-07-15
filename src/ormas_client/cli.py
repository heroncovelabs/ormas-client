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


def _platform_tag() -> str:
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "wsl2"
    return "linux"


def _runner_start(args: argparse.Namespace) -> int:
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
        print(json.dumps({"task_id": task_id, "status": "no_lease"}, indent=2, sort_keys=True))
        return 0

    if args.dry_run:
        # Dry-run is the default and must never execute or edit anything.
        print(
            json.dumps(
                {"task_id": task_id, "status": "dry_run", "base_commit": base_commit},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    # --no-dry-run: get a routing preview from the gateway WITHOUT sending the
    # repo path, source, OpenRouter key or any other local secret.
    preview = OrmasClient(config.gateway_url, access_key).routing_preview(
        {"task": args.brief, "task_type": "coding", "policy": "route"}
    )
    tuple_id = str(preview.get("tuple") or args.tuple or "")
    if tuple_id not in CERTIFIED_TUPLES:
        raise SystemExit(f"selected tuple '{tuple_id}' is not certified; refusing to execute")

    openrouter_key = get_secret("openrouter-key")
    if not openrouter_key:
        raise SystemExit("missing OpenRouter key; run `ormas runner login` first")

    lease.setdefault("task_id", task_id)
    lease.setdefault("runner_id", runner_id)
    lease.setdefault("base_commit", base_commit)
    lease.setdefault("brief", args.brief)
    lease.setdefault("verify_command", args.verify_command)
    lease.setdefault("allowed_paths", list(args.allowed_path))
    summary = execute_lease(
        client=control,
        repo=repo,
        lease=lease,
        tuple_id=tuple_id,
        openrouter_key=openrouter_key,
        budget_usd=args.cost_cap,
    )
    print(
        f"task={summary['task_id']} tuple={summary['tuple']} "
        f"worktree={summary['worktree']} result_commit={summary['result_commit']}"
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
