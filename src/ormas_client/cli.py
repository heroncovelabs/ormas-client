from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Config, get_secret, load, save, set_secret
from .http import OrmasClient


def _login(args: argparse.Namespace) -> int:
    if not args.access_key.startswith("tb_live_"):
        raise SystemExit("access key must start with tb_live_")
    if not args.openrouter_key.startswith("sk-or-"):
        raise SystemExit("OpenRouter key must start with sk-or-")
    set_secret("access-key", args.access_key)
    set_secret("openrouter-key", args.openrouter_key)
    config = load()
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
    config.repositories[args.alias] = str(path)
    save(config)
    print(f"Registered {args.alias} -> {path}")
    return 0


def _runner_start(args: argparse.Namespace) -> int:
    config = load()
    repo_path = config.repositories.get(args.repo_alias)
    if not repo_path:
        raise SystemExit(f"unknown repository alias: {args.repo_alias}")
    access_key = get_secret("access-key")
    if not access_key:
        raise SystemExit("not logged in; run `ormas runner login` first")
    payload = {
        "task": args.brief,
        "task_type": "coding",
        "policy": "draft",
        "dry_run": args.dry_run,
        "repo_path": repo_path,
        "worker_enabled": not args.dry_run,
        "cost_cap_usd": args.cost_cap,
    }
    result = OrmasClient(config.gateway_url, access_key).submit(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


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
    login.add_argument("--gateway-url", default="https://ormas-gateway.fly.dev")
    login.set_defaults(func=_login)
    start = runner_sub.add_parser("start")
    start.add_argument("--repo-alias", required=True)
    start.add_argument("--brief", required=True)
    start.add_argument("--cost-cap", type=float, default=0.25)
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
