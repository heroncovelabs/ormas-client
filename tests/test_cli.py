from pathlib import Path

import pytest

from ormas_client.cli import build_parser


def test_public_command_contract() -> None:
    parser = build_parser()
    login = parser.parse_args(
        ["runner", "login", "--access-key", "tb_live_example", "--openrouter-key", "sk-or-v1-x"]
    )
    assert login.access_key == "tb_live_example"
    repo = parser.parse_args(["repo", "add", "trading", "/tmp/repo"])
    assert repo.alias == "trading"
    start = parser.parse_args(["runner", "start", "--repo-alias", "trading", "--brief", "fix"])
    assert start.dry_run is True
    assert start.verify_command
    assert start.allowed_path


def test_repo_add_rejects_non_git_directory(tmp_path: Path) -> None:
    args = build_parser().parse_args(["repo", "add", "bad", str(tmp_path)])
    with pytest.raises(SystemExit, match="not a Git repository"):
        args.func(args)
