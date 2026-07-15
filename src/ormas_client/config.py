from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import keyring
from platformdirs import user_config_path

SERVICE = "ormas-client"


@dataclass
class Config:
    # The portal control plane hosts the strict runner-v1 lifecycle.
    control_url: str = "https://ormas.ai"
    # The gateway hosts routing previews for --no-dry-run.
    gateway_url: str = "https://ormas-gateway.fly.dev"
    runner_id: str | None = None
    # Repository aliases map to a local path and a stable derived repo_id.
    # This file NEVER stores access or provider keys — those live in the keyring.
    repositories: dict[str, str] = field(default_factory=dict)
    repo_ids: dict[str, str] = field(default_factory=dict)


def config_path() -> Path:
    return user_config_path("ormas", appauthor=False) / "config.json"


def load() -> Config:
    path = config_path()
    if not path.exists():
        return Config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Config(
        control_url=str(raw.get("control_url") or Config.control_url),
        gateway_url=str(raw.get("gateway_url") or Config.gateway_url),
        runner_id=raw.get("runner_id") or None,
        repositories=dict(raw.get("repositories") or {}),
        repo_ids=dict(raw.get("repo_ids") or {}),
    )


def save(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "control_url": config.control_url,
                "gateway_url": config.gateway_url,
                "runner_id": config.runner_id,
                "repositories": config.repositories,
                "repo_ids": config.repo_ids,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)

