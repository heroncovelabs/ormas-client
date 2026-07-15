from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import keyring
from platformdirs import user_config_path

SERVICE = "ormas-client"


@dataclass
class Config:
    gateway_url: str = "https://ormas-gateway.fly.dev"
    repositories: dict[str, str] = field(default_factory=dict)


def config_path() -> Path:
    return user_config_path("ormas", appauthor=False) / "config.json"


def load() -> Config:
    path = config_path()
    if not path.exists():
        return Config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Config(
        gateway_url=str(raw.get("gateway_url") or Config.gateway_url),
        repositories=dict(raw.get("repositories") or {}),
    )


def save(config: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"gateway_url": config.gateway_url, "repositories": config.repositories},
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

