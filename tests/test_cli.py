from pathlib import Path

import pytest

from ormas_client.cli import build_parser
from ormas_client.config import Config


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


@pytest.mark.parametrize(
    ("host", "expected_command"),
    [
        ("codex", ["codex", "mcp", "add", "ormas", "--", "ormas-mcp"]),
        (
            "claude",
            ["claude", "mcp", "add", "--scope", "project", "ormas", "--", "ormas-mcp"],
        ),
    ],
)
def test_connect_registers_current_repo_and_configures_inline_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    host: str,
    expected_command: list[str],
) -> None:
    repo = tmp_path / "customer-repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)

    config = Config()
    saved: list[Config] = []
    commands: list[list[str]] = []
    monkeypatch.setattr("ormas_client.cli.load", lambda: config)
    monkeypatch.setattr("ormas_client.cli.save", saved.append)

    def fake_run(argv: list[str], **_: object) -> object:
        commands.append(argv)
        return object()

    monkeypatch.setattr("ormas_client.cli.subprocess.run", fake_run)

    args = build_parser().parse_args(["connect", "--host", host])
    assert args.func(args) == 0

    assert saved == [config]
    assert config.repositories == {"customer-repo": str(repo)}
    assert config.repo_ids["customer-repo"].startswith("repo-")
    assert commands == [
        ["git", "-C", str(repo), "status", "--porcelain"],
        expected_command,
    ]
    output = capsys.readouterr().out
    assert "Connected customer-repo" in output
    assert f"Restart {host}" in output
