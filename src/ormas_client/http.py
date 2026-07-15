from __future__ import annotations

from typing import Any

import httpx


class OrmasClient:
    def __init__(self, base_url: str, access_key: str) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_key}"},
            timeout=60.0,
        )

    def health(self) -> dict[str, Any]:
        response = self._client.get("/health")
        response.raise_for_status()
        return response.json()

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post("/ormas/task", json=payload)
        response.raise_for_status()
        return response.json()

