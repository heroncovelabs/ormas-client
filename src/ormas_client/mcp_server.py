from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .config import get_secret, load
from .http import OrmasClient

mcp = FastMCP("ormas")


@mcp.tool()
def ormas_submit_task(task: str, repo_alias: str, dry_run: bool = True) -> dict:
    config = load()
    repo_path = config.repositories.get(repo_alias)
    if not repo_path:
        raise ValueError(f"unknown repository alias: {repo_alias}")
    access_key = get_secret("access-key")
    if not access_key:
        raise ValueError("not logged in")
    return OrmasClient(config.gateway_url, access_key).submit(
        {
            "task": task,
            "task_type": "coding",
            "policy": "draft",
            "dry_run": dry_run,
            "repo_path": repo_path,
            "worker_enabled": not dry_run,
            "cost_cap_usd": 0.25,
        }
    )


def main() -> None:
    mcp.run(transport="stdio")

